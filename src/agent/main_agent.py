# =============================================================================
# ★ 主 Agent 入口模块 —— ERP 采购智能助手，Graph Factory 模式实现用户级沙箱隔离
# =============================================================================
"""
主 Agent 入口模块。

使用 DeepAgents `create_deep_agent` 将所有组件串联为一个可运行的
ERP 采购智能助手。采用 Graph Factory 模式：启动时预计算可复用组件，
每次请求基于 per-user 沙箱轻量创建 agent graph，实现用户级沙箱隔离。

使用方式:
    from agent.main_agent import precompute_agent_context, create_main_agent

    # 启动时
    precomputed = await precompute_agent_context()

    # 每次请求
    agent_graph = await create_main_agent(
        config,
        sandbox_backend=user_sandbox,
        precomputed=precomputed,
    )
"""
import dataclasses
import logging
import os
import sys
from dataclasses import field


def _setup_logging() -> None:
    """根据 APP_ENV 环境变量初始化全局日志配置。

    生产环境写入文件（erp_agent.log），级别 INFO；
    开发环境输出到标准输出，级别 ERROR。
    """
    env = os.environ.get("APP_ENV", "development")
    if env == "production":
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            filename="erp_agent.log",
            datefmt="%Y-%m-%d %H:%M:%S",
            filemode="a",
        )
    else:
        logging.basicConfig(
            level=logging.ERROR,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stdout,
        )


# 模块加载时即初始化日志
_setup_logging()
logger = logging.getLogger(__name__)


# =============================================================================
# ★ 1. PrecomputedContext —— 启动时预计算的可复用组件（不依赖用户沙箱）
# =============================================================================
@dataclasses
class PrecomputedContext:
    """
    Phase 2/3/5 预计算结果的不可变容器。

    仅包含不依赖 sandbox_backend 的组件（MCP 工具、图表工具、YAML 配置）
    sandbox 依赖的工具 （assign_skill\ download_sandbox_file）和 backend 依赖的中间件在 create_main_agent() 中按请求动态创建
    """

    # MCP 工具列表
    all_mcp_tools: list = field(default_factory=list)
    # 分析师 MCP 工具列表
    analyst_mcp_tools: list = field(default_factory=list)
    # 订单 MCP 工具列表
    order_mcp_tools: list = field(default_factory=list)
    # 图表 MCP 工具列表
    chart_mcp_tools: list = field(default_factory=list)
    # 额外的 MCP 工具列表
    extra_mcp_tools: list = field(default_factory=list)

    # 图表生成器
    generate_visualization: object = None
    # 原始子代理配置列表
    raw_subagent_configs: list = field(default_factory=list)


async def precompute_agent_context() -> PrecomputedContext:
    """
    Phase 2/3/5 预计算，启动时执行一次，所有请求复用。

     加载 MCP 工具、合并可视化工具、加载子 Agent YAML 配置。
    这些操作不依赖用户沙箱，结果可在所有请求间共享。

    Returns:
        PrecomputedContext: 包含所有预计算组件的数据类。

    Raises:
        RuntimeError: 当 MCP 工具加载失败时抛出。
    """

    logger.info("=== 预计算 Agent 上下文（Phase 2/3/5）===")

    # ---- Phase 2: MCP 工具加载 ----
    logger.info("Phase 2:加载 MCP 工具...")
    try:
        # todo
        all_mcp_tools, analyst_mcp_tools, order_mcp_tools, chart_mcp_tools = (
            await load_mcp_tools()
        )
    except  Exception:
        logger.exception("MCP 工具加载失败")
        raise RuntimeError("MCP 工具加载失败，无法预计算")

    # ---- Phase 3: 可视化工具合并（将多个图表子工具合并为一个 generate_visualization 工具）----
    logger.info("Phase 3: 合并可视化工具 (26→1)...")
    # todo
    generate_visualization, extra_mcp_tools = create_generate_chart_tool(chart_mcp_tools)
    if extra_mcp_tools:
        logger.info(f"  保留独立工具: {[t.name for t in extra_mcp_tools]}")

    # ---- Phase 5: 子 Agent YAML 配置加载 ----
    logger.info("Phase 5: 加载子 Agent YAML 配置...")
    # todo
    raw_configs = load_subagent_configs()
    if not raw_configs:
        logger.warning("  未找到任何子 Agent 配置")
    else:
        logger.info(f"  已加载 {len(raw_configs)} 个子 Agent 配置")

    logger.info("=== 预计算完成 ===")
    return PrecomputedContext(
        all_mcp_tools=all_mcp_tools,
        analyst_mcp_tools=analyst_mcp_tools,
        order_mcp_tools=order_mcp_tools,
        chart_mcp_tools=chart_mcp_tools,
        extra_mcp_tools=extra_mcp_tools,
        generate_visualization=generate_visualization,
        raw_subagent_configs=raw_configs,
    )
