"""
摘要中间件工厂
基于 DeepAgents 0.4 内置的 create_summarization_tool_middleware 创建，
提供以下能力：
1. 自动摘要：上下文接近模型上限时自动压缩历史消息。
2. 主动摘要：提供 compact_conversation 工具，Agent 可在关键节点（如收到子 Agent 报告后）主动调用。
"""
from typing import Union

from deepagents.backends import CompositeBackend
from deepagents.middleware import SummarizationToolMiddleware, create_summarization_tool_middleware
from langchain_core.language_models import BaseChatModel


# =============================================================================
# ★ 1. 工厂函数 —— build_summarization_middleware
# =============================================================================
def build_summarization_middleware(
    backend: CompositeBackend,
    model: Union[str, BaseChatModel] = "DeepSeek-V4-Flash",
) -> SummarizationToolMiddleware:
    """
    构建摘要工具中间件。

    该中间件是一个 SummarizationToolMiddleware 实例，内部自动包含了一个
    SummarizationMiddleware（负责自动摘要），并额外提供了一个名为
    `compact_conversation` 的工具，供 Agent 主动触发对话压缩。

    使用场景：
    - 长对话场景：当对话历史接近模型上下文窗口上限时，自动压缩早期消息
    - 子 Agent 场景：收到子 Agent 的长报告后，Agent 可主动调用 compact_conversation
      压缩对话历史，释放上下文空间

    参数:
        backend: 沙箱后端（用于持��化被��缩的完整对话历史）。
        model: 用于生成摘要的模型，可以是字符串标识或模型实例。
               默认为 SUMMARY_MODEL（DeepSeek-V4-Flash），也可以传入其他模型覆盖。

    返回:
        SummarizationToolMiddleware: 可直接传入 create_deep_agent 的 middleware 列表。

    """
    # 该工厂函数会自动创建一个 SummarizationMiddleware 并将其嵌入到
    # SummarizationToolMiddleware 中。触发阈值等参数使用框架默认值，
    # 通常为上下文达到 85% 时触发自动摘要，这已满足多数生产场景。
    return create_summarization_tool_middleware(
        model=model,
        backend=backend,
    )