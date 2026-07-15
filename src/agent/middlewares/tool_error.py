"""
工具错误处理中间件。

wrap_tool_call 捕获所有工具调用异常，转换为 ToolMessage（status="error"），
避免单个工具失败导致整个 Agent 运行崩溃。
"""
import json
import logging
from typing import Callable, Awaitable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


# =============================================================================
# ★ 1. 辅助函数 —— 从 ToolCallRequest 提取信息
# =============================================================================
def _tool_call_id(request: ToolCallRequest) -> str | None:
    """
    从 ToolCallRequest 中提取 tool_call 的 Id。

    兼容 tool_call 为 dict 或对象两种形式。
    LangChain 不同版本中 tool_call 字段格式可能不同：
    - 旧版本：tool_call 作为嵌套对象
    - 新版本：tool_call 可能直接是 dict

    Args:
        request: 工具调用请求对象
    Returns:
        工具调用 ID 字符串，若无法获取则返回 None。
    """
    tc = getattr(request.tool_call, "tool_call", None)
    if isinstance(tc, dict):
        return tc.get("id")
    return getattr(tc, "id", None)


def _tool_name(request: ToolCallRequest) -> str | None:
    """
    从 ToolCallRequest 中提取工具名称。

    兼容 tool_call 为 dict 或对象两种形式。
    Args:
        request: 工具调用请求对象
    Returns:
        工具名称字符串，若无法获取则返回 None。
    """
    tc = getattr(request, "tool_call", None)
    if isinstance(tc, dict):
        return tc.get("name")
    return getattr(tc, "name", None)


# =============================================================================
# ★ 2. 辅助函数 —— 构建错误负载
# =============================================================================
def _build_error_payload(e: Exception, request: ToolCallRequest) -> dict[str, str]:
    """将异常信息格式化为 JSON 可序列化的字典，用于构造错误 ToolMessage 的内容。

     Args:
         e: 捕获到的异常对象。
         request: 原始工具调用请求，用于附加工具名称。

     Returns:
         包含 error（错误信息，截断至 500 字符）、error_type（异常类型名）、
         status（固定为 "error"）和可选 name（工具名称）的字典。
     """
    data: dict[str, str] = {
        "error": str(e)[:500],  # 截断防止错误信息过长
        "error_type": type(e).__name__,
        "status": "error",
    }

    name = _tool_name(request)
    if name:
        data["name"] = name
    return data


# =============================================================================
# ★ 3. 类 ToolErrorMiddleware —— 工具错误处理中间件
# =============================================================================
class ToolErrorMiddleware(AgentMiddleware):
    """
    捕获工具调用异常 → 转换为错误 ToolMessage。

    设计理念：
    - 单个工具失败不应该导致整个 Agent 崩溃
    - 通过将异常转换为 status="error" 的 ToolMessage，Agent 可以看到错误详情
    - Agent 可选择重试、跳过或使用其他工具完成目标

    工作机制：
    - wrap_tool_call 在工具执行前包装 handler
    - 成功时返回 handler 结果，失败时返回包含错误信息的 ToolMessage
    """

    state_name = AgentState

    # ------------------------------------------------------------------
    # ★ 同步钩子 —— 工具调用拦截
    # ------------------------------------------------------------------
    def wrap_tool_call(
            self,
            request: ToolCallRequest,
            handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """
        同步工具调用包装：执行 handler，失败时返回错误 ToolMessage。

        Args:
            request: 工具调用请求，包含工具名、参数和调用 Id。
            handler: 实际执行工具调用的回调函数。

        Returns:
            成功时返回 handler 的原始结果；失败时返回 status="error" 的 ToolMessage。
        """

        try:
            return handler(request)
        except Exception as e:
            logger.warning(
                "工具调用异常: tool=%s, error=%s: %s",
                _tool_name(request), type(e).__name__, str(e)[:200],
            )
            # 将异常信息作为 ToolMessage 内容返回，使 Agent 能看到错误详情
            return ToolMessage(
                content=json.dumps(_build_error_payload(e, request), ensure_ascii=False),
                tool_call_id=_tool_call_id(request),
                status="error",
            )

    # ------------------------------------------------------------------
    # ★ 异步钩子 —— 工具调用拦截
    # ------------------------------------------------------------------
    async def awrap_tool_call(
            self,
            request: ToolCallRequest,
            handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """
        异步工具调用包装：执行 handler，失败时返回错误 ToolMessage。

        与同步版本区别：
        - 使用 JSON 序列化错误负载（结构化，便于 Agent 解析）
        - 通过 await 调用异步 handler

        Args:
            request: 工具调用请求，包含工具名、参数和调用 Id。
            handler: 实际执行工具调用的异步回调函数。

        Returns:
            成功时返回 handler 的结果；失败时返回 status="error" 的 ToolMessage。
        """
        try:
            return await handler(request)
        except Exception as e:

            logger.warning(
                "工具调用异常: tool=%s, error=%s: %s",
                _tool_name(request), type(e).__name__, str(e)[:200],
            )
            # 将异常序列化为 JSON 结构化返回，包含 error、error_type、status、name 字段
            return ToolMessage(
                content=json.dumps(_build_error_payload(e, request), ensure_ascii=False),
                tool_call_id=_tool_call_id(request),
                status="error",
            )
