"""
运行时上下文注入中间件。

从 runtime.context（ProcurementContext）中提取 user_id / username，
在 Agent 启动时以 SystemMessage 形式注入到对话中。Agent 无需调用工具
即可知道当前用户身份，从而正确读写 /memories/{user_id}/preferences.md。

使用方式:
    from agent.middlewares.context_injection import ContextInjectionMiddleware
    middleware = ContextInjectionMiddleware()
"""
import logging
from typing import Dict, Any, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)


# =============================================================================
# ★ 1. 类 ContextInjectionMiddleware —— 运行时上下文注入中间件
# =============================================================================
class ContextInjectionMiddleware(AgentMiddleware):
    """
    将 runtime.context 中的 user_id / username 注入到对话开头

    通过 before_agent 钩子在 Agent 执行前注入一条 SystemMessage
    告知 Agent 当前用户身份和偏好文件路径
    """

    # ------------------------------------------------------------------
    # ★ 同步钩子 —— 注入用户上下文
    # ------------------------------------------------------------------
    def before_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
        同步版本：注入用户上下文 SystemMessage。

        从 runtime.context 提取 user_id / username，构建一条包含用户身份信息和偏好文件路径的 SystemMessage，追加到消息列表的开头

        Args：
            state : Agent 当前状态
            runtime : 运行时上下文，包含 context (ProcurementContext)对象
        Returns:
              包含注入消息的字典 {"messages":[SystemMessage(...)]},
              若 context 不可用或无 user_id 则返回 None。
        """
        ctx = getattr(runtime, "context", None)
        if ctx is None:
            logger.warning("ContextInjectionMiddleware: runtime.context 为 None，跳过上下文注入")
            return None
        user_id = ctx.get("user_id", None)
        if not user_id:
            logger.warning("ContextInjectionMiddleware: runtime.context 中没有 user_id，跳过上下文注入")
            return None
        username = ctx.get("username", None) or user_id

        logger.info(f"ContextInjectionMiddleware: 注入用户上下文 user_id={user_id}, username={username}")

        notice = (
            f"【系统上下文】\n"
            f"当前用户 user_id: {user_id}\n"
            f"当前用户 username: {username}\n"
            f"用户偏好文件路径: /memories/{user_id}/preferences.md\n"
            f"\n请首先使用 read_file 读取上述偏好文件了解用户偏好。"
            f"\n（recent_suppliers 和 recent_queries 由系统自动维护，你无需手动更新）"
        )
        return {"messages": [SystemMessage(content=notice)]}

    # ------------------------------------------------------------------
    # ★ 异步钩子 —— 注入用户上下文
    # ------------------------------------------------------------------
    async def abefore_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
        异步版本：注入用户上下文 SystemMessage。
        从 runtime.context 提取 user_id / username，构建一条包含用户身份信息和偏好文件路径的 SystemMessage，追加到消息列表的开头

        Args：
            state : Agent 当前状态
            runtime : 运行时上下文，包含 context (ProcurementContext)对象
        Returns:
              包含注入消息的字典 {"messages":[SystemMessage(...)]},
              若 context 不可用或无 user_id 则返回 None。
        """
        # 与同步版本逻辑相同
        return self.before_agent(state, runtime)
