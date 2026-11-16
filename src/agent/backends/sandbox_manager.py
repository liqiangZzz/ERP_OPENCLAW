# =============================================================================
# ★ 模块：沙箱管理器 — Per-User 沙箱生命周期管理 + 预热机制
# =============================================================================
"""
沙箱管理器 —— 用户级沙箱生命周期管理 + 预热机制。

核心职责：
1. 隔离粒度：基于 user_id 维护独立沙箱，同一用户的多个会话（Thread）共享。
2. 生命周期（四状态机）：预热认领 -> 内存缓存（Ping验证） -> MongoDB 重连 -> 全新创建。
3. 预热机制（Pre-warm）：启动时预建沙箱，消除首位用户的冷启动延迟。
4. 持久化：沙箱元数据持久化至 MongoDB，服务重启后可无损重连。

依赖约束：
- 底层通过 setup_sandbox 和 SandboxSync 与 OpenSandbox API 交互。
- 所有对外暴露的沙箱实例均包装为 SandboxBackendProxy，以支持运行期的热替换。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient
from pymongo.collection import Collection

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.config import SANDBOX_CONFIG

logger = logging.getLogger(__name__)

# =============================================================================
# ★ 1. MongoDB 常量
# =============================================================================
# MongoDB 中存储沙箱注册信息（user_id -> sandbox_id 映射）的集合名称
SANDBOX_COLLECTION = "sandbox_registry"

# =============================================================================
# ★ 2. 全局状态
# =============================================================================
# 内存缓存：加速同一用户的后续请求查找 (user_id -> Proxy)
SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}

# 预热沙箱：预先创建好的沙箱，第一个用户到达时直接分配，避免冷启动延迟
_warm_reserve: SandboxBackendProxy | None = None
# 预热沙箱操作的异步锁：防止多个用户并发认领或并发补充预热资源
_warm_lock = asyncio.Lock()

# MongoDB 客户端连接实例
_mongo_client: Optional[MongoClient] = None
# MongoDB 集合引用（初始化后赋值）
_collection = None


# =============================================================================
# ★ 3. 内部辅助函数
# =============================================================================
def _sandbox_collection() -> Collection:
    """
    获取 MongoDB 沙箱集合的快捷方法。

    Raises:
        RuntimeError: 若管理器尚未初始化。
    """
    if _mongo_client is None:
        raise RuntimeError("沙箱管理器未初始化，请先调用 initialize()")
    from agent.config import MONGODB_DB_NAME
    # 返回 MongoDB 中的沙箱集合
    db = _mongo_client.get_database(MONGODB_DB_NAME)
    return db.get_collection(SANDBOX_COLLECTION)


# =============================================================================
# ★ 4. 公开 API
# =============================================================================

async def initialize(mongo_client: MongoClient):
    """
     启动时初始化 MongoDB 连接

     建立与 MongoDB 的连接，并在 user_id 字段上创建唯一的索引
     确保“一个用户只对应一个沙箱记录”，防止并发写冲突。

     Args：
        mongo_client: MongoDB 客户端实例
    """
    global _mongo_client, _collection

    _mongo_client = mongo_client
    _collection = _sandbox_collection()

    # 建立唯一索引，确保并发下同一个 user_id 只会有一条记录
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

        #  预热沙箱
        sandbox_backend = await asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)
        #  预热沙箱代理，包装为 Proxy 并放入预热池
        _warm_reserve = SandboxBackendProxy(sandbox_backend)
        logger.info("沙箱预热就绪：%s", sandbox_backend.id)
    except Exception:
        logger.warning("预热沙箱失败，将在第一个用户连接时创建", exc_info=True)
        _warm_reserve = None


async def ensure_sandbox_for_user(user_id: str) -> SandboxBackendProxy:
    """
    获取或创建某个用户的沙箱（核心状态机）。

    状态机流程（按优先级顺序）:
    【状态1】内存缓存命中 -> Ping 健康检查 -> 可达则直接返回；不可达则触发重建。
    【状态2】预热池认领   -> 零等待分配并更新 DB；后台异步补充新预热池。
    【状态3】DB 记录重连 -> 尝试连接 Mongo 记录的老沙箱；失败则重建。
    【状态4】全新创建     -> 无任何记录，直接创建新沙箱。

    为什么分 3 层缓存?
    - 预热沙箱：消除首个用户的冷启动延迟
    - 内存缓存：同一用户的多次请求无需查库/重连
    - MongoDB 持久化：服务重启后仍可恢复用户沙箱

    Args:
        user_id: 用户唯一标识符。

    Returns:
        可用的 SandboxBackendProxy 实例。
    """

    # 状态1. 内存缓存命中：直接验证可用性
    proxy = SANDBOX_BACKENDS.get(user_id)
    if proxy is not None:
        try:
            # 使用线程池执行 ping 操作，防止阻塞事件循环
            await asyncio.to_thread(proxy.execute, "echo ok")
            logger.info("用户 %s 命中沙箱缓存: %s", user_id, proxy.id)
            return proxy
        except Exception:
            logger.warning("用户 %s 的沙箱 %s 不可达，重建中...", user_id, proxy.id)
            #  重建沙箱
            return await _recreate_sandbox(user_id, proxy)

    # 状态2. 认领预热沙箱：加锁防止并发争抢
    async with _warm_lock:
        global _warm_reserve
        if _warm_reserve is not None:
            proxy = _warm_reserve  # 认领预热沙箱
            _warm_reserve = None  # 预热沙箱被认领后，清空预热沙箱
            sandbox_id = proxy.id  # 认领的预热沙箱 ID

            SANDBOX_BACKENDS[user_id] = proxy  # 缓存用户沙箱代理
            # 持久化绑定关系（upsert: 存在则更新，不存在则插入）
            _sandbox_collection().update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "sandbox_id": sandbox_id,
                        "updated_at": datetime.now(timezone.utc),
                        "created_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True
            )
            logger.info("用户 %s 认领预热沙箱: %s", user_id, sandbox_id)
            # 异步触发后台补充新预热池沙箱：不阻塞当前用户的返回，确保响应及时
            try:
                asyncio.create_task(_replenish_warm())
            except Exception as e:
                logger.error("创建预热沙箱任务失败: %s", e)
            return proxy

    # 状态3. 从 MongoDB 重连已有沙箱：尝试连接记录的老沙箱，失败则重建新沙箱
    doc = _sandbox_collection().find_one({"user_id": user_id})
    if doc and doc.get("sandbox_id"):
        sandbox_id = doc["sandbox_id"]
        logger.info("用户 %s 尝试重连沙箱: %s", user_id, sandbox_id)

        from agent.backends.sandbox_setup import setup_sandbox
        try:
            # 尝试连接已有沙箱
            sandbox_backend = await asyncio.to_thread(
                setup_sandbox, SANDBOX_CONFIG, sandbox_id=sandbox_id
            )
        except Exception:
            logger.warning("用户 %s 的沙箱 %s 重连失败，将重新创建", user_id, sandbox_id)
            # 重连失败，创建新沙箱
            return await _create_sandbox_for_user(user_id)

        # 验证连接沙箱是否可达
        try:
            await asyncio.to_thread(sandbox_backend.execute, "echo ok")
        except Exception:
            logger.warning("已连接的沙箱 %s 不可达，创建新沙箱", sandbox_id)
            # 沙箱不可达，标记重建（原沙箱 ID 会由 _recreate_sandbox 尝试删除）
            return await _recreate_sandbox(user_id, None)

        # 重连成功，包装为 Proxy 并缓存更新活跃时间
        proxy = SandboxBackendProxy(sandbox_backend)
        SANDBOX_BACKENDS[user_id] = proxy  # 缓存用户沙箱代理
        # 持久化绑定关系（upsert: 存在则更新，不存在则插入）
        _sandbox_collection().update_one(
            {"user_id": user_id},
            {"$set": {"updated_at": datetime.now(timezone.utc)}}
        )
        return proxy

    # 状态4. 无记录，创建新沙箱
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
    """
    销毁某用户的沙箱（内存缓存 + 远程实例 + DB 记录）。

    Args:
        user_id: 用户唯一标识符。
    """

    # 从内存缓存中移除沙箱代理
    proxy = SANDBOX_BACKENDS.pop(user_id, None)
    if proxy is not None:
        try:
            from opensandbox import SandboxSync
            # 销毁远端物理沙箱
            await asyncio.to_thread(SandboxSync.delete, proxy.id)
        except Exception:
            logger.warning("删除沙箱 %s 失败", proxy.id, exc_info=True)

    # 从 MongoDB 中删除用户记录
    _sandbox_collection().delete_one({"user_id": user_id})
    logger.info("用户 %s 沙箱已清理", user_id)


async def shutdown(cleanup_remote: bool = False) -> None:
    """
    优雅关闭：释放沙箱管理器资源。

    策略：
    - 预热沙箱（无主）始终直接销毁。
    - 用户沙箱默认只清内存（保留远端和 DB），以便下次启动重连；
    - 除非显式传入 cleanup_remote=True（如测试环境或资源彻底回收）。
    """

    global _warm_reserve, _mongo_client
    logger.info("正在清理所有沙箱...")

    if _warm_reserve is not None:
        try:
            from opensandbox import SandboxSync
            # 删除预热沙箱
            await asyncio.to_thread(SandboxSync.delete, _warm_reserve.id)
        except Exception as e:
            # 清理阶段删除失败不应中断后续清理，但要留痕
            logger.warning(f"删除预热沙箱失败（忽略）: {e}")
        _warm_reserve = None

    #  清理用户沙箱
    if cleanup_remote:
        # 危险操作：摧毁所有用户的真实沙箱
        for user_id in list(SANDBOX_BACKENDS.keys()):
            await cleanup_user(user_id)
    else:
        # 推荐策略：仅清空内存，下一次启动根据 DB 重连
        SANDBOX_BACKENDS.clear()
        logger.info("已保留用户远程沙箱，供下次启动重连")

    if _mongo_client is not None:
        _mongo_client.close()
        _mongo_client = None

    logger.info("沙箱管理器已关闭")


# =============================================================================
# ★ 5. 内部函数
# =============================================================================

async def _create_sandbox_for_user(user_id: str) -> SandboxBackendProxy:
    """
    创建新沙箱并绑定到用户，调用 sandbox_setup 创建全新沙箱，注册到内存缓存和 MongoDB

    Args:
        user_id: 用户唯一标识符。

    Returns:
        新创建的 SandboxBackendProxy 实例。

    """
    from agent.backends.sandbox_setup import setup_sandbox

    logger.info("为用户 %s 创建新沙箱...", user_id)
    sandbox_backend = await asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)
    sandbox_id = sandbox_backend.id

    # 创建 Proxy 并缓存到内存
    proxy = SandboxBackendProxy(sandbox_backend)
    SANDBOX_BACKENDS[user_id] = proxy  # 缓存用户沙箱代理

    # 持久化绑定关系（upsert: 存在则更新，不存在则插入）
    _sandbox_collection().update_one(
        {"user_id": user_id},
        {
            "$set": {
                "sandbox_id": sandbox_id,
                "updated_at": datetime.now(timezone.utc),
                "created_at": datetime.now(timezone.utc),
            }
        },
        upsert=True
    )
    logger.info("用户 %s 沙箱创建完成： %s", user_id, sandbox_id)
    return proxy


async def _recreate_sandbox(
        user_id: str, existing_proxy: SandboxBackendProxy | None,
) -> SandboxBackendProxy:
    """
    重建沙箱（原沙箱不可达时）

    核心机制：如果已有 Proxy，则利用 replace_backend() 热替换底层实例，保证上层 Graph 持有的 Proxy 引用不丢失。
            同时尝试 MongoDB 中异步删除遗留的旧物理沙箱，然后更新 DB 绑定。

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

    #  热替换或新建 proxy，保持外部引用不变，仅替换内部 backend
    if existing_proxy is not None:
        existing_proxy.replace_backend(sandbox_backend)
    else:
        existing_proxy = SandboxBackendProxy(sandbox_backend)
        SANDBOX_BACKENDS[user_id] = existing_proxy  # 缓存用户沙箱代理

    # 异步清理遗留的旧物理沙箱
    old_doc = _sandbox_collection().find_one({"user_id": user_id})
    if old_doc and old_doc.get("sandbox_id"):
        old_id = old_doc["sandbox_id"]
        try:
            from opensandbox import SandboxSync
            await asyncio.to_thread(SandboxSync.delete, old_id)
        except Exception:
            logger.warning("删除旧沙箱 %s 失败", old_id, exc_info=True)
            pass

    # 更新 DB 中的 sandbox_id 指向新实例
    _sandbox_collection().update_one(
        {"user_id": user_id},
        {
            "$set": {
                "sandbox_id": sandbox_id,
                "updated_at": datetime.now(timezone.utc),
            }
        }
    )

    logger.info("用户 %s 沙箱重建完成: %s", user_id, sandbox_id)
    return existing_proxy


async def _replenish_warm() -> None:
    """
    后台补充预热沙箱（被认领后异步触发）

    当预热沙箱被第一个用户认领后，在后台异步创建一个新的预热沙箱，
    以便后续用户也能享受零等待的冷启动体验。
    如果当前已有预热沙箱则直接返回。

    关键点：必须使用双重检查锁（Double-Checked Locking），
    防止在并发认领时，后台启动了多个补充任务，浪费资源。
    """
    global _warm_reserve

    # 已有预热沙箱，无需补充
    if _warm_reserve is not None:
        return

    try:
        from agent.backends.sandbox_setup import setup_sandbox

        # 创建新的预热沙箱，耗时的物理创建（不加锁，允许并发创建以提升响应速度，但只留第一个结果）
        sandbox_backend = await asyncio.to_thread(setup_sandbox, SANDBOX_CONFIG)

        # 临界区：加锁二次确认，确保只有一个任务创建成功并绑定
        async with _warm_lock:
            # 双重检查：获取锁后再次确认仍为空（防止并发）
            if _warm_reserve is None:
                _warm_reserve = SandboxBackendProxy(sandbox_backend)
                logger.info("后台补充预热沙箱就绪: %s", sandbox_backend.id)
    except Exception:
        logger.warning("后台补充预热沙箱失败", exc_info=True)
