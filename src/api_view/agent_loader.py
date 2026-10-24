import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from deepagents.middleware.async_subagents import AsyncSubAgentState
from langgraph.graph import END, START, StateGraph
from pymongo import MongoClient

from agent.backends import sandbox_manager
from agent.config import CHECKPOINTER, MONGODB_CHECKPOINT_COLLECTION, MONGODB_DB_NAME
from agent.env_utils import CLEANUP_SANDBOX_ON_SHUTDOWN, PREWARM_SANDBOX
from agent.main_agent import (
    PrecomputedContext,
    create_main_agent,
    precompute_agent_context,
)
from api_view.web_config import MONGODB_URI


# =============================================================================
# ★ 0. 只读状态图 —— 必须走 LangGraph 状态 API 才能拿到完整会话
# =============================================================================
"""
为什么不能直接读 checkpoint["channel_values"]：

    langgraph-checkpoint 4.x 起改用 delta channel 存储 —— 每个 checkpoint 文档
    只保存「本步更新过的通道」。实测一个 61 条消息的真实会话，其最新 checkpoint 的
    channel_values 里只有 async_tasks / branch:to:* 这类键，messages 通道根本不在，
    完整状态需要回放 checkpoint_writes。而 langgraph-checkpoint-mongodb 0.4.0
    完全没有 delta 处理（不实现 BaseCheckpointSaver.get_delta_channel_history），
    所以 CheckpointTuple.checkpoint 拿到的是稀疏值。

    后果：/api/history 靠 get_current_messages() 判断会话是否有消息，永远得到 []，
    于是所有会话都被 if messages: 过滤掉 —— 表现就是「会话没保存、刷新就没了」，
    但数据其实一条没丢（61 条都在 checkpoint_writes 里）。

    正确读法是走 Pregel 的状态 API，它会自己回放 delta。这里复用一个只读最小图：
    状态 schema 直接用 deepagents 的 AsyncSubAgentState（含 messages 与 async_tasks
    及其 reducer），与同一个 CHECKPOINTER 编译后按 thread_id 读快照。实测同一条
    会话：raw channel_values -> messages 缺失；本 reader -> 61 条消息 + 1 个异步任务。
"""
_state_reader = (
    StateGraph(AsyncSubAgentState)
    .add_node("_read_state", lambda state: {})
    .add_edge(START, "_read_state")
    .add_edge("_read_state", END)
    .compile(checkpointer=CHECKPOINTER)
)


# =============================================================================
# ★ 1. _StateSnapshot 数据结构 —— CheckpointTuple 到 StateSnapshot 的轻量适配器
# =============================================================================
# CheckpointTuple 到 StateSnapshot 的轻量适配器
@dataclass
class _StateSnapshot:
    """状态快照数据结构，用于从 checkpoint 中提取关键信息。"""
    values: dict  # 通道值（channel_values），包含当前 agent 状态的所有数据
    config: dict  # 会话配置信息（包含 thread_id、user_id 等）
    created_at: datetime | str | None = None  # 创建时间戳
    parent_config: dict | None = None  # 父级 checkpoint 配置，用于追踪状态链
    metadata: dict | None = None  # 附加元数据


class AgentLoader:
    """
    Agent 加载器单例器

    复杂管理 Agent 生命周期、MongoDB 链接、沙箱管理和会话相关操作。
    采用 Graph Factory：每次请求基于 per-user 沙箱创建 agent graph
    """

    # 单例实例
    _instance: Optional['AgentLoader'] = None
    # MongoDB 客户端
    _mongodb_client: Optional[MongoClient] = None
    # 初始化标志
    _initialized: bool = False
    # 预计算上下文（MCP 工具/YAML 配置）
    _precomputed: PrecomputedContext = None
    # 最近创建的 agent graph 引用（用于状态查询；所有 graph 共享同一 checkpointer）
    _agent = None

    # =====================================================================
    # ★ 2.1 单例构造
    # =====================================================================
    def __new__(cls):
        """单例模式： 确保全局只有一个 AgentLoader 实实例。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # =====================================================================
    # ★ 2.2 初始化与关闭
    # =====================================================================

    # 启动初始化
    async def initialize(self):
        """
        启动时初始化： MongoDB + 沙箱管理器 + 预计算 + 预热沙箱

        预计算 （MCP 工具/YAML 配置） 执行一次，所有请求复用。
        预热沙箱爱启动阶段完成（～15秒），首个用户连接时零等待。
        """

        # 防重入：已初始化且预计算上下文存在时，直接返回缓存的 agent，避免重复初始化
        if self._initialized and self._precomputed is not None:
            return self._agent

        print("[AgentLoader] 开始初始化...")

        try:

            # MongoDB 链接
            self._mongodb_client = MongoClient(MONGODB_URI)

            # 2.  沙箱管理器初始化（MongoDB 连接 + 索引）
            await sandbox_manager.initialize(self._mongodb_client)

            # 3. 预计算 MCP 工具 + 图表工具 + YAML 配置
            self._precomputed = await precompute_agent_context()
            print("[AgentLoader] 预计算完成（MCP 工具 + 图表工具 + YAML 配置）")

            # 4. 沙箱预热可选；本地开发默认关闭，避免拖慢 Python 服务启动。
            if PREWARM_SANDBOX:
                await sandbox_manager.pre_warm()
            else:
                print("[AgentLoader] 已跳过沙箱预热（PREWARM_SANDBOX=false）")

            self._initialized = True
            print("[AgentLoader] 初始化完成")

        except Exception:
            if self._mongodb_client is not None:
                self._mongodb_client.close()
                self._mongodb_client = None
            raise

    # 销毁用户沙箱
    async def cleanup_user(self, user_id: str) -> None:
        """销毁用户沙箱。"""
        await  sandbox_manager.cleanup_user(user_id)

    # 关闭清理
    async def shutdown(self) -> None:
        """
        应用关闭时清理所有沙箱和 MongoDB 连接
        """
        print("[AgentLoader] 正在关闭...")
        await sandbox_manager.shutdown(
            cleanup_remote=CLEANUP_SANDBOX_ON_SHUTDOWN,
        )
        if self._mongodb_client is not None:
            self._mongodb_client.close()
            self._mongodb_client = None
            print("[AgentLoader] MongoDB 连接已关闭")

        self._initialized = False
        self._precomputed = None
        self._agent = None

    # =====================================================================
    # ★ 2.3 Agent 实例管理
    # =====================================================================

    # 获取最近的 Agent 实例
    @property
    def agent(self):
        """
        获取最近创建的 Agent 实例（用于状态查询）

        所有 agent graph 共享同一 MongoDBSaver checkpointer，
        因此状态查询不依赖于特定用户的 graph。
        """
        if self._agent is None:
            raise RuntimeError("Agent 未初始化，请先调用 initialize() 方法")
        return self._agent

    # 获取 per-user agent graph
    async def get_agent_for_user(self, user_id: str):
        """
        获取 per-user agent graph

        每次请求调用： 获取/创建 per-user 沙箱 → 创建 agent graph。
        同一个用户的多个 thread 共享沙箱（缓存命中 < 0.1s）

        Args:
            user_id: 用户唯一标识
        Returns:
            CompiledStateGraph：该用户的 agent graph
        """

        # 1. 获取 / 创建用户沙箱
        sandbox_backend = await sandbox_manager.ensure_sandbox_for_user(user_id)
        # 2. 创建配置
        config = self.create_config(user_id=user_id)
        config["configurable"]["user_id"] = user_id

        # 3. 创建 agent graph
        agent_graph = await create_main_agent(
            config,
            sandbox_backend=sandbox_backend,
            precomputed=self._precomputed,
        )

        # 4.  保留引用用于状态查询 （所有 graph 共享同一个 chéckpointer）
        self._agent = agent_graph
        return agent_graph

    # =====================================================================
    # ★ 2.4 配置与会话
    # =====================================================================

    # 创建 LangGraph 配置字典
    def create_config(
            self,
            thread_id: Optional[str] = None,
            user_id: Optional[str] = None,
            **kwargs
    ) -> Dict[str, Any]:
        """
        创建 LangGraph 运行所需的配置字典

        如果未提供 thread_id 会自定生成一个 UUID

        Args:
            thread_id: 会话线程 ID，不传则自动生成 UUID
            user_id: 用户 ID
            **kwargs: 其他可传入的可选配置项
        Returns:
           包含 configurable 键的标准 LangGraph 配置字典
        """
        return {
            "configurable": {
                "thread_id": thread_id or str(uuid.uuid4()),
                "user_id": user_id,
                **kwargs
            }
        }

    # =====================================================================
    # ★ 2.5 状态与消息查询
    # =====================================================================

    # 获取历史状态快照列表
    async def get_state_history(
            self,
            thread_id: str,
            limit: int = 50
    ) -> List[Any]:
        """
        获取指定会话的历史状态快照列表。

        走 _state_reader 的状态 API（会回放 delta channel），不再直接
        CHECKPOINTER.alist() 读原始 channel_values —— 后者每一步只含「本步更新的
        通道」，拿到的历史条目基本是残缺的（原因见模块顶部说明）。

        Args:
            thread_id: 会话线程 ID
            limit: 最多返回的快照数量，默认 50

        Returns:
            _StateSnapshot 对象列表，按时间倒序
        """
        config = {"configurable": {"thread_id": thread_id}}
        states = []
        try:
            async for snap in _state_reader.aget_state_history(config, limit=limit):
                states.append(_StateSnapshot(
                    values=dict(snap.values or {}),
                    config=snap.config,
                    created_at=snap.created_at,
                    parent_config=snap.parent_config,
                ))
        except Exception as e:
            print(f"[AgentLoader] 获取会话 {thread_id} 历史失败: {e}")
        return states

    # 读取 thread 的完整状态快照（回放 delta channel 后）
    async def read_thread_state(self, thread_id: str):
        """
        通过只读状态图获取该会话回放后的完整状态。

        Args:
            thread_id: 会话线程 ID

        Returns:
            langgraph StateSnapshot；无 checkpoint 或读取失败时返回 None
        """
        try:
            return await _state_reader.aget_state({"configurable": {"thread_id": thread_id}})
        except Exception as e:
            print(f"[AgentLoader] 读取会话 {thread_id} 状态失败: {e}")
            return None

    # 获取当前消息列表（从 checkpoint）
    async def get_current_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        """
        获取指定会话当前的消息列表。

        走 _state_reader 的状态 API 而不是直接读 checkpoint["channel_values"]，
        原因见模块顶部说明（langgraph-checkpoint 4.x 的 delta channel 存储）。

        Args:
            thread_id: 会话线程 ID

        Returns:
            消息字典列表，若会话不存在或出错则返回空列表
        """
        snapshot = await self.read_thread_state(thread_id)
        if snapshot and snapshot.values:
            return list(snapshot.values.get("messages", []) or [])
        return []

    # =====================================================================
    # ★ 2.5.1 异步子 Agent 任务列表
    # =====================================================================

    async def get_async_tasks(self, thread_id: str) -> Dict[str, Any]:
        """
        读取该会话 checkpoint 中 AsyncSubAgentMiddleware 维护的 async_tasks。

        为什么需要它：子 Agent 挂在另一个 Agent Protocol server 上（spec 带 url），
        deepagents 会主动丢弃父 Agent 的 assistant_id，「任务完成自动唤醒父 Agent」
        这条路在本地 FastAPI 部署下不成立，只能靠前端轮询。本方法就是刷新页面后
        恢复后台任务列表的数据来源。

        Args:
            thread_id: 会话线程 ID

        Returns:
            {task_id: 任务字段字典}；无任务或读取失败时返回空字典
        """
        snapshot = await self.read_thread_state(thread_id)
        tasks = (snapshot.values or {}).get("async_tasks") if snapshot else None
        if not isinstance(tasks, dict):
            return {}
        # 只保留可 JSON 序列化的标量字段，避免 checkpoint 里混入对象导致
        # FastAPI response_model 校验失败
        return {
            task_id: {
                key: value
                for key, value in (task or {}).items()
                if value is None or isinstance(value, (str, int, float, bool))
            }
            for task_id, task in tasks.items()
        }

    # =====================================================================
    # ★ 2.6 会话查询与删除
    # =====================================================================

    # 获取所有 thread_id
    def get_all_thread_ids(self) -> List[str]:
        """
        从 MongoDB 中提取所有有效的会话 thread_id。

        过滤掉空值、星号等无效标识。

        Returns:
            有效的 thread_id 字符串列表
        """
        # MongoDB 未连接时返回空列表
        if self._mongodb_client is None:
            return []

        db = self._mongodb_client[MONGODB_DB_NAME]
        collection = db[MONGODB_CHECKPOINT_COLLECTION]
        thread_ids = collection.distinct("thread_id")
        return [tid for tid in thread_ids if tid and tid != "*" and tid != "" and tid is not None]

    # 获取会话最后更新时间
    def get_session_updated_at(self, thread_id: str) -> datetime:
        """
        获取指定会话的最后更新时间。

        通过 MongoDB 中该 thread_id 最新文档的 _id（ObjectId）的 generation_time 获取。
        若查询失败或文档不存在，返回当前时间。
        Args:
            thread_id: 会话线程 ID

        Returns:
            最后更新时间 datetime 对象
        """
        # MongoDB 未连接时返回当前时间
        if self._mongodb_client is None:
            return datetime.now()
        db = self._mongodb_client[MONGODB_DB_NAME]
        collection = db[MONGODB_CHECKPOINT_COLLECTION]

        try:
            latest_doc = collection.find_one(
                {"thread_id": thread_id}, sort=[("_id", -1)]
            )
            if latest_doc:
                if "_id" in latest_doc and hasattr(latest_doc["_id"], "generation_time"):
                    return latest_doc["_id"].generation_time
                elif "-id" in latest_doc:
                    import bson
                    if isinstance(latest_doc["_id"], bson.objectid.ObjectId):
                        return latest_doc["_id"].generation_time
            return datetime.now()
        except Exception as e:
            print(f"[AgentLoader] 获取会话时间失败: {e}")
            return datetime.now()

    # 删除会话（checkpoint + 展示消息）
    async def delete_session(self, thread_id: str) -> bool:
        """
        删除指定会话的所有数据（包含 checkpoint 和展示消息）。

        Args:
            thread_id: 会话线程 ID

        Returns:
            删除成功返回 True，失败返回 False
        """
        # MongoDB 未连接时返回失败
        if self._mongodb_client is None:
            return False

        db = self._mongodb_client[MONGODB_DB_NAME]
        collection = db[MONGODB_CHECKPOINT_COLLECTION]
        try:
            result = collection.delete_many({"thread_id": thread_id})
            display_collection = db["session_display_messages"]
            display_result = display_collection.delete_many({"thread_id": thread_id})
            print(
                f"[AgentLoader] 已删除会话 {thread_id}，checkpoint {result.deleted_count} 条，展示消息 {display_result.deleted_count} 条")
            return True
        except Exception as e:
            print(f"[AgentLoader] 删除会话失败: {e}")
            return False

    # =====================================================================
    # ★ 2.7 完整展示消息存取
    # =====================================================================

    # 消息字段最大长度限制，超过此长度的文本将被截断
    _MAX_FIELD_LENGTH = 500_000

    # 截断过长字段
    @classmethod
    def _truncate_message_fields(cls, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        截断消息中过长的文本字段，防止单条文档超过 MongoDB 的 16MB 限制。

        Args:
            msg: 消息字典

        Returns:
            处理后的消息字典（可能已截断）
        """
        for field in ("text", "content", "args"):
            if field in msg and isinstance(msg[field], str) and len(msg[field]) > cls._MAX_FIELD_LENGTH:
                msg[field] = msg[field][:cls._MAX_FIELD_LENGTH] + "\n\n...(内容过长已截断)"
        return msg

    # 保存展示消息到 MongoDB
    async def save_display_messages(self, thread_id: str, messages: List[Dict[str, Any]]) -> bool:
        """
        保存会话的展示消息到 MongoDB。

        先清除该 thread_id 的旧消息，再批量写入新消息。每条消息会先经过截断处理。

        Args:
            thread_id: 会话线程 ID
            messages: 消息字典列表

        Returns:
            保存成功返回 True，失败返回 False
        """
        # MongoDB 未连接时返回失败
        if self._mongodb_client is None:
            return False
        try:
            db = self._mongodb_client[MONGODB_DB_NAME]
            collection = db["session_display_messages"]
            try:
                collection.create_index([("thread_id", 1), ("index", 1)])
            except Exception:
                pass
            collection.delete_many({"thread_id": thread_id})
            if messages:
                now = datetime.now()
                docs = []
                for i, msg in enumerate(messages):
                    # 截断过长字段（避免传入 i 参数，方法只接受 msg）
                    msg = self._truncate_message_fields(msg)
                    docs.append({
                        "thread_id": thread_id,
                        "index": i,
                        "message": msg,
                        "created_at": now
                    })
                collection.insert_many(docs)
                print(f"[AgentLoader] 已保存 {len(docs)} 条展示消息，thread_id={thread_id}")
            return True
        except Exception as e:
            print(f"[AgentLoader] 保存展示消息失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    # 从 MongoDB 读取展示消息
    async def get_display_messages(self, thread_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        从 MongoDB 读取指定会话的展示消息列表。

        按 index 字段升序排列，确保消息顺序正确。

        Args:
            thread_id: 会话线程 ID

        Returns:
            消息字典列表，若无数据则返回 None，出错返回 None
        """
        # MongoDB 未连接时返回 None
        if self._mongodb_client is None:
            return None

        try:
            db = self._mongodb_client[MONGODB_DB_NAME]
            collection = db["session_display_messages"]
            cursor = collection.find({"thread_id": thread_id}).sort("index", 1)
            docs = list(cursor)
            if not docs:
                return None
            messages = [doc["message"] for doc in docs]
            print(f"[AgentLoader] 已读取 {len(messages)} 条展示消息，thread_id={thread_id}")
            return messages
        except Exception as e:
            print(f"[AgentLoader] 获取展示消息失败: {e}")
            import traceback
            traceback.print_exc()
            return None


# =============================================================================
# ★ 3. 全局单例实例
# =============================================================================
# 全局单例实例
agent_loader = AgentLoader()
