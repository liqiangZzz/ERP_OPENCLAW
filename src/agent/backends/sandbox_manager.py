# =============================================================================
# ★ 模块：沙箱管理器 — Per-User 沙箱生命周期管理 + 预热机制
# =============================================================================
"""
沙箱管理器 — Per-User 沙箱生命周期管理 + 预热机制。

每个用户维护一个独立的 OpenSandbox，同一个用户的多条对话线程共享。
启动时预热一个沙箱，第一个用户连接时直接认领，无需等待创建。

沙箱 ID 通过 MongoDB 持久化，重启后可重连到已有沙箱。
"""
import asyncio
from datetime import datetime, timezone
import logging

from pymongo import MongoClient

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.config import SANDBOX_CONFIG

logger = logging.getLogger(__name__)

# =============================================================================
# ★ 1. MongoDB 常量
# =============================================================================
# MongoDB 中存储沙箱注册信息的集合名称
SANDBOX_COLLECTION = "sandbox_registry"

# =============================================================================
# ★ 2. 全局状态
# =============================================================================
# 用户 ID → 沙箱代理的内存缓存，用于快速查找
SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}

# 预热沙箱：预先创建好的沙箱，第一个用户到达时直接分配，避免冷启动延迟
_warm_reserve: SandboxBackendProxy | None = None
# 预热沙箱操作的异步锁，防止并发竞争
_warm_lock = asyncio.Lock()


# =============================================================================
# ★ 3. 内部辅助函数
# =============================================================================
def _sandbox_collection():
    """
    获取 MongoDB 沙箱集合。 若未初始化则抛出 RunTimeError

    Returns:
        pymongo.collection.Collection 实例。
    """
    if _mongo_client is None:
        raise RuntimeError("沙箱管理器未初始化，请先调用 initialize()")
    from agent.config import MONGODB_DB_NAME
    # 返回 MongoDB 中的沙箱集合
    return _mongo_client[MONGODB_DB_NAME][SANDBOX_COLLECTION]


# =============================================================================
# ★ 4. 公开 API
# =============================================================================

async def initialize(mongo_client: MongoClient):
    """
     启动时初始化 MongoDB 连接

     建立与 MongoDB 的连接，并在 user_id 字段上创建唯一的索引
     确保每个用户最多绑定一个沙箱记录

     Args：
        mongo_client (pymongo.MongoClient): MongoDB 客户端实例
    """
    global _mongo_client, _collection

    _mongo_client = mongo_client
    _collection = _sandbox_collection()
    _collection.create_index("user_id", unique=True)
    logger.info("沙箱管理器已初始化")


async def pre_warm() -> None:
    """
    启动时预热沙箱，首个用户连接时直接认领。失败不阻塞启动。

    预先创建一个沙箱实例并保存为 _warm_reserve,
    当第一个用户请求沙箱时可以直接分配，无需等待创建过程。
    """
    global _warm_reserve
    logger.info("正在预热沙箱...")
    try:
        from agent.backends.sandbox_setup import setup_sandbox

        sandbox_backend = await  asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)
        _warm_reserve = SandboxBackendProxy(sandbox_backend)
        logger.info("沙箱预热就绪：%s", sandbox_backend.id)
    except Exception:
        logger.warning("预热沙箱失败，将在第一个用户连接时创建", exc_info=True)
        _warm_reserve = None


async def ensure_sandbox_for_user(user_id: str) -> SandboxBackendProxy:
    """
    获取或创建某个用户的沙箱

     状态机流程:
        0. _warm_reserve 有货 → 认领分配给 user
        1. 内存缓存命中 → ping 健康检查 → 失败则重建
        2. MongoDB 有 sandbox_id → connect(id) → 失败则重建
        3. 无任何记录 → 创建新沙箱

    Args:
        user_id: 用户唯一标识符。

    Returns:
        可用的 SandboxBackendProxy 实例。
    """

    # 状态1. 内存缓存命中
    proxy = SANDBOX_BACKENDS.get(user_id)
    if proxy is not None:
        try:
            await asyncio.to_thread(proxy.execute, "echo ok")
            logger.info("用户 %s 命中沙箱缓存: %s", user_id, proxy.id)
            return proxy
        except Exception:
            logger.warning("用户 %s 的沙箱 %s 不可达，重建中...", user_id, proxy.id)
            #  重建沙箱
            return await _recreate_sandbox(user_id, proxy)

    # 状态0. 认领预热沙箱
    async with _warm_lock:
        global _warm_reserve
        if _warm_reserve is not None:
            proxy = _warm_reserve
            _warm_reserve = None
            sandbox_id = proxy.id

            SANDBOX_BACKENDS[user_id] = proxy
            _sandbox_collection().update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "sandbox_id": sandbox_id,
                        "updated_at": datetime.now(timezone.utc),
                        "created_at": datetime.now(timezone.utc),
                    }},
                upsert=True
            )
            logger.info("用户 %s 认领预热沙箱: %s", user_id, sandbox_id)
            # 后台补充预热沙箱（被认领后异步触发）
            asyncio.create_task(_replenish_warm())
            return proxy

    # 状态 2: MongoDB 重连
    doc = _sandbox_collection().find_one({"user_id": user_id})
    if doc and doc.get("sandbox_id"):
        sandbox_id = doc["sandbox_id"]
        logger.info("用户 %s 尝试重连沙箱： %s", user_id, sandbox_id)
        try:
            from agent.backends.sandbox_setup import setup_sandbox
            await asyncio.to_thread(
                setup_sandbox, SANDBOX_CONFIG, sandbox_id=sandbox_id
            )
        except Exception:
            logger.warning("用户 %s 的沙箱 %s 重连失败，将重新创建", user_id, sandbox_id)
            #  重建沙箱
            return await _create_sandbox_for_user(user_id)

        try:
            await asyncio.to_thread(setup_sandbox.execute, proxy.id, "echo ok")
        except Exception:
            logger.warning("已连接的沙箱 %s 不可达，创建新沙箱", sandbox_id)

            #  重建沙箱（原沙箱不可达时）
            return await _recreate_sandbox(user_id, None)

        proxy = SandboxBackendProxy(setup_sandbox)
        SANDBOX_BACKENDS[user_id] = proxy
        _sandbox_collection().update_one(
            {"user_id": user_id},
            {"$set": {"updated_at": datetime.now(timezone.utc)}}
        )
        return proxy

    # 状态3 ：无记录，创建新沙箱
    return await _create_sandbox_for_user(user_id)


async def ping_user_sandbox(user_id: str) -> bool:
    """
    公开的沙箱健康检查，供中间件调用。

    Args:
        user_id: 用户唯一标识符。

    Returns:
        bool: 沙箱是否可达。
    """
    proxy = SANDBOX_BACKENDS.get(user_id)
    if proxy is None:
        return False

    try:
        await asyncio.to_thread(proxy.execute, "echo ok")
        return True
    except Exception:
        return False


async def recreate_user_sandbox(user_id: str) -> SandboxBackendProxy:
    """
       公开的沙箱重建方法，供 SandboxHealthMiddleware 在检测到沙箱不可用时调用。

       创建新沙箱（含 skills 播种 + venv），替换 proxy 内的 backend，
       删除旧沙箱，更新 MongoDB 绑定。不包含 AGENTS.md 上传，由调用方负责。

       Args:
           user_id: 用户唯一标识符。

       Returns:
           新的 SandboxBackendProxy 实例。
    """
    proxy = SANDBOX_BACKENDS.get(user_id)
    return await _recreate_sandbox(user_id, proxy)


async def cleanup_user(user_id: str) -> None:
    """销毁某用户的沙箱。

      从内存缓存中移除沙箱代理，调用 OpenSandbox API 删除远程沙箱
      并从 MongoDB 中清除注册记录

      Args:
        user_id (str): 用户唯一标识符。
    """

    proxy = SANDBOX_BACKENDS.pop(user_id, None)
    if proxy is not None:
        try:
            from opensandbox import SandboxSync
            await asyncio.to_thread(SandboxSync.delete, proxy.id)
        except Exception:
            logger.warning("删除沙箱 %s 失败", proxy.id, exc_info=True)

    _sandbox_collection().delete_one({"user_id": user_id})
    logger.info("已删除用户 %s 的沙箱 %s", user_id, proxy.id if proxy else "N/A")


async def shutdown() -> None:
    """
    清理所有沙箱（含预热），释放资源

    按顺序销毁预热沙箱、所有用户沙箱，最后关闭 MongoDB 连接
    通常在应用关闭时调用。
    """

    global _warm_reserve, _mongo_client
    logger.info("正在清理所有沙箱...")

    if _warm_reserve is not None:
        try:
            from opensandbox import SandboxSync
            await asyncio.to_thread(SandboxSync.delete, _warm_reserve.id)
        except Exception:
            pass
        _warm_reserve = None

    for user_id in list(SANDBOX_BACKENDS.keys()):
        await cleanup_user(user_id)

    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None

    logger.info("沙箱管理器已关闭")


# =============================================================================
# ★ 5. 内部函数
# =============================================================================

async def _create_sandbox_for_user(user_id: str) -> SandboxBackendProxy:
    """
    创建新沙箱并绑定到用户

    调用 sandbox_setup 创建全新沙箱，注册到内存缓存和 MongoDB

    Args:
        user_id: 用户唯一标识符。

    Returns:
        新创建的 SandboxBackendProxy 实例。

    """
    from agent.backends.sandbox_setup import setup_sandbox

    logger.info("为用户 %s 创建新沙箱...", user_id)
    sandbox_backend = await asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)
    sandbox_id = sandbox_backend.id

    proxy = SandboxBackendProxy(setup_sandbox)
    SANDBOX_BACKENDS[user_id] = proxy

    _sandbox_collection().update_one(
        {"user_id": user_id},
        {"$set": {
            "sandbox_id": sandbox_id,
            "updated_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True
    )
    logger.info("用户 %s 沙箱创建完成： %s", user_id, sandbox_id)
    return proxy


async def _recreate_sandbox(
        user_id: str, existing_proxy: SandboxBackendProxy | None,
) -> SandboxBackendProxy:
    """
    重建沙箱（原沙箱不可达时）

    创建全新沙箱，若存在旧 proxy 则热替换其内部 backend
    同事尝试 MongoDB 中记录的旧沙箱

    Args:
        user_id: 用户唯一标识符。
        existing_proxy: 旧沙箱代理（可能已不可达），若为 None 则新建 proxy。
    Returns:
         重建后的 SandboxBackendProxy 实例。
    """
    from agent.backends.sandbox_setup import setup_sandbox

    logger.info("为用户 %s 重建沙箱...", user_id)

    sandbox_backend = await asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)
    sandbox_id = sandbox_backend.id

    if existing_proxy is not None:
        # 热替换；保持 proxy 对象不变，仅替换内部 backend
        existing_proxy.replace_backend(sandbox_backend)
    else:
        existing_proxy = SandboxBackendProxy(sandbox_backend)
        SANDBOX_BACKENDS[user_id] = existing_proxy

    # 尝试删除 MongoDb 中记录的旧沙箱
    old_doc = _sandbox_collection().find_one({"user_id": user_id})
    if old_doc and old_doc.get("sandbox_id"):
        old_id = old_doc["sandbox_id"]
        try:
            from opensandbox import SandboxSync
            await asyncio.to_thread(SandboxSync.delete, old_id)
        except Exception:
            logger.warning("删除旧沙箱 %s 失败", old_id, exc_info=True)
            pass

    _sandbox_collection().update_one(
        {"user_id": user_id},
        {"$set": {
            "sandbox_id": sandbox_id,
            "updated_at": datetime.now(timezone.utc),
        }}
    )

    logger.info("用户 %s 沙箱重建完成: %s", user_id, sandbox_id)
    return existing_proxy


async def _replenish_warm() -> None:
    """
    后台补充预热沙箱（被认领后异步触发）

      当预热沙箱被第一个用户认领后，在后台异步创建一个新的预热沙箱，
    以便后续用户也能享受零等待的冷启动体验。
    如果当前已有预热沙箱则直接返回。
    """

    global _warm_reserve
    if _warm_reserve is not None:
        return

    try:
        from agent.backends.sandbox_setup import setup_sandbox
        sandbox_backend = await asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)
        async with _warm_lock:
            if _warm_reserve is not None:
                _warm_reserve = SandboxBackendProxy(sandbox_backend)
                logger.info("后台补充预热沙箱就绪: %s", sandbox_backend.id)
    except Exception:
        logger.warning("后台补充预热沙箱失败", exc_info=True)
