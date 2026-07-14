"""
子 Agent 中间件配置。

提供标准中间件的工厂函数，在创建 Agent 时注入。
针对不同子 Agent 的业务特点（分析型 vs 事务型）配置不同的中间件组合。
"""
from deepagents.backends import CompositeBackend
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel

from agent.middlewares.tools_summarization import create_summarization_tool_middleware


# =============================================================================
# ★ 1. create_analyst_middleware —— 分析型子 Agent 中间件（含摘要+限制）
# =============================================================================
def create_analyst_middleware(
    model: BaseChatModel,
    backend: CompositeBackend,
) -> list:
    """
    为 procurement-analyst 子 Agent 创建中间件列表。

    分析型 Agent 需要摘要中间件来压缩上下文，防止长对话导致 token 溢出。

    包含：
     - SummarizationToolMiddleware：阶段完成后主动压缩上下文
     - ModelCallLimitMiddleware：防止无限循环（最多 50 次模型调用）
     - ToolCallLimitMiddleware：防止工具调用爆炸（最多 200 次工具调用）

    Args:
        model: 用于摘要生成的模型（建议用小模型如 deepseek-v4-flash）
        backend: 文件系统后端，用于保存中间件生成的摘要文件

    Returns:
        中间件实例列表
    """
    return [
        create_summarization_tool_middleware(model, backend),
        ModelCallLimitMiddleware(run_limit=50),
        ToolCallLimitMiddleware(run_limit=200),
    ]


# =============================================================================
# ★ 2. create_order_middleware —— 事务型子 Agent 中间件（仅调用限制）
# =============================================================================
def create_order_middleware() -> list:
    """
    为 procurement-order 子 Agent 创建中间件列表。

    订单操作通常是简单

    事务型 Agent 只需要调用限制，不需要摘要中间件。

    包含：
     - ModelCallLimitMiddleware： 防止无限虚幻（最多 50 次模型调用）
     - ToolCallLimitMiddleware： 防止工具调用爆炸（最多 200 次工具调用）

    Returns：
        中间件实例列表
    """
    return [
        ModelCallLimitMiddleware(run_limit=50),
        ToolCallLimitMiddleware(run_limit=200),
    ]

