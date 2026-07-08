from typing import Optional

from pymongo import MongoClient

from agent.backends import sandbox_manager
from api_view.web_config import MONGODB_URI


class AgentLoader:
    """
    Agent 加载器单例器

    复杂管理 Agent 生命周期、MongoDB 链接、沙箱管理和会话相关操作。
    采用 Graph Factory：每次请求基于 per-user 沙箱创建 agent graph
    """

    _instance: Optional['AgentLoader'] = None
    _mongodb_client: Optional[MongoClient] = None
    _initialized: bool = False  # 布尔标志，标记是否已完成过初始化
    _precomputed = PrecomputedContext = None  # 预计算上下文（MCP工具/YAML配置）是否已生成
    # 最近创建的 agent graph （用于状态查询；所有 graph 共享同一 checkpointer）
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

            # 2. todo 沙箱管理器初始化（MongoDB 连接 + 索引）
            await sandbox_manager.initialize(self._mongodb_client)

            # 3.todo  预计算 MCP 工具 + 图表工具 + YAML 配置
            self._precomputed = await precompute_agent_context()
            print("[AgentLoader] 预计算完成（MCP 工具 + 图表工具 + YAML 配置）")

            # 4. todo 预热第一个沙箱（阻塞 ~15s，首个用户无需等待创建）
            await sandbox_manager.pre_warm()

            self._initialized = True
            print("[AgentLoader] 初始化完成")

        except Exception:
            if self._mongodb_client is not None:
                self._mongodb_client.close()
                self._mongodb_client = None
            raise

    # todo 销毁用户沙箱
    async def cleanup_user(self, user_id: str) -> None:
        """销毁用户沙箱"""
        await  sandbox_manager.cleanup_user(user_id)

    async def shutdown(self) -> None:
        """
        应用关闭时清理所有沙箱和 MongoDB 连接
        """
        print("[AgentLoader] 正在关闭...")
        await sandbox_manager.shutsown()  # todo
        if self._mongodb_client is not None:
            self._mongodb_client.close()
            self._mongodb_client = None
            print("[AgentLoader] MongoDB 连接已关闭")

        self._initialized = False
        self._precomputed = None
        self._agent = None


# 全局单例实例，供其他模块直接导入使用
agent_loader = AgentLoader()
