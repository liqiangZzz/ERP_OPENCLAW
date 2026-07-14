"""
沙箱熔断中间件。

before_model 检查最近消息中的沙箱相关错误，连续失败 ≥ 阈值时
跳转到 end 并注入通知消息，防止无限恢复循环。

与 SandboxHealthMiddleware 配合：
- SandboxHealthMiddleware：主动 ping → 自动恢复
- SandboxCircuitBreakerMiddleware：恢复失败次数超限 → 熔断
"""
import logging
from typing import Any, Sequence

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# =============================================================================
# ★ 1. 常量定义
# =============================================================================
# 熔断触发阈值：连续沙箱错误次数达到此值时触发熔断
THRESHOLD = 2

# 用于标记熔断消息的特殊标识符，用于重置连续错误计数器
CIRCUIT_BREAKER_MARKER = "SandboxCircuitBreaker"

# 用于匹配沙箱相关错误的关键字列表（区分大小写检查时统一转小写比对）
_SANDBOX_ERROR_KEYWORDS = [
    "SandboxBackendProxy",
    "sandbox",
    "SandboxError",
    "ConnectionError",
    "ConnectionRefusedError",
    "TimeoutError",
    "execute",
    "沙箱",
    "不可达",
    "连接",
    "超时",
]


# =============================================================================
# ★ 2. 类 SandboxCircuitBreakerMiddleware —— 沙箱熔断中间件
# =============================================================================
class SandboxCircuitBreakerMiddleware(AgentMiddleware):
    """
    连续沙箱错误超时阈值 → 熔断，跳转到 end。

    在 before_model 阶段检查消息历史，若联系沙箱错误数超过阈值，
    则通过 jump_to="end" 中断 Agent 执行，并注入一条说明消息。
    """

    state_schema = AgentState

    def __init__(self, threshold: int = THRESHOLD):
        super().__init__()
        self.threshold = threshold  # 连续沙箱错误次数阈值，超过此值触发熔断

    # ------------------------------------------------------------------
    # ★ 2.1同步钩子 —— 熔断检查
    # ------------------------------------------------------------------
    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """
        在模型调用前检查是否需要熔断

        若连续沙箱错误次数超过阈值，返回 {"jump_to": "end", "messages": [...]} 以终止 Agent 执行。

        Args:
            state: 当前Agent 状态，包含消息列表
            runtime: 运行时上下文
        Returns：
             需要熔断时返回包含跳转指令和通知消息的字典；否则返回 None。
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        count = _count_consecutive_sandbox_errors(messages)
        # 连续沙箱错误次数超过阈值时才触发熔断
        if count > self.threshold:
            return None

        logger.warning(
            "沙箱熔断: 连续 %d 次沙箱错误超过阈值 %d", count, self.threshold,
        )
        content = (
            f"{CIRCUIT_BREAKER_MARKER}: 连续 {count} 次沙箱操作失败，已触发熔断保护。"
            "请检查沙箱服务状态后重试，或联系管理员。"
        )
        # 注入 AIMessage 并跳转到 end 节点，终止当前 Agent 执行
        return {"jump_to": "end", "messages": [AIMessage(content=content)]}

    # ------------------------------------------------------------------
    # ★ 异步钩子 —— 熔断检查
    # ------------------------------------------------------------------
    @hook_config(can_jump_to=["end"])
    async def abefore_model(
            self, state: AgentState, runtime: Runtime,
    ) -> dict[str, Any] | None:
        """异步版本的 before_model，逻辑与同步版本完全一致。"""
        return self.before_model(state, runtime)


# =============================================================================
# ★ 3. 辅助函数 —— 统计连续沙箱错误
# =============================================================================
def _count_consecutive_sandbox_errors(
        messages: Sequence[BaseMessage],
) -> int:
    """
    从消息列表末尾向前统计连续沙箱错误的 ToolMessage 数量。

    遍历逻辑（从后往前）：
    1. 遇到 ToolMessage 且 status="error" 且内容包含沙箱关键字 → 计数+1，继续回溯
    2. 遇到 ToolMessage 但不是沙箱错误 → 停止回溯，之前的连续错误有效
    3. 遇到 AIMessage 且包含熔断标记 → 说明之前已触发过熔断，重置计数为 0
    4. 遇到 human/system 消息 → 停止回溯（跨用户会话不连续）

    Args:
        messages: 对话消息列表，按时间正序排列。
    Returns:
         末尾连续的沙箱错误 ToolMessage 数量。
    """
    count = 0
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            content = getattr(msg, "content", "")
            # 兼容 content 为 list 类型的情况（如多模态内容块）
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            status = getattr(msg, "status", None)
            # 仅统计 status="error" 且包含沙箱关键字的 ToolMessage
            if status == "error" and _looks_like_sandbox_error(str(content)):
                count += 1
                continue
            # 遇到非沙箱错误的 ToolMessage，停止回溯
            break

        elif isinstance(msg, AIMessage):
            content = getattr(msg, "content", "")
            text = content if isinstance(content, str) else str(content)

            # 如果 AI 消息包含熔断标记，说明之前已触发过熔断，重置计数器
            if CIRCUIT_BREAKER_MARKER in text:
                count = 0
        elif getattr(msg, "type", "") in {"human", "system"}:
            # 遇到新的用户消息，停止回溯（不跨越会话统计）
            break
    return count


# =============================================================================
# ★ 4. 辅助函数 —— 沙箱错误检测
# =============================================================================
def _looks_like_sandbox_error(text: str) -> bool:
    """
    判断文本是否包含沙箱相关的错误关键字
    Args:
        text: 待检查的文本（通常为 ToolMessage 的内容）。
    Returns:
        文本中包含任意一个沙箱错误关键字则返回 True。
    """

    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in _SANDBOX_ERROR_KEYWORDS)
