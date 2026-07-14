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

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.runnables import RunnableConfig

from agent.config import SKILLS_STORE_NAMESPACE, LOCAL_AGENTS_MD, STORE, DOWNLOAD_DIR, SUMMARY_MODEL, MAIN_MODEL, \
    AGENTS_MD_FILENAME, CHECKPOINTER
from agent.tools.mcp_client import load_mcp_tools
from agent.memory.prompts import system_prompt
from agent.middlewares.context_injection import ContextInjectionMiddleware
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from agent.middlewares.sandbox_breaker import SandboxCircuitBreakerMiddleware
from agent.middlewares.sandbox_health import SandboxHealthMiddleware
from agent.middlewares.skills_sync import SkillsSyncMiddleware
from agent.middlewares.tool_error import ToolErrorMiddleware
from agent.middlewares.tools_summarization import build_summarization_middleware
from agent.middlewares.user_skills_restore import UserSkillsRestoreMiddleware
from agent.scheam import ProcurementContext
from agent.subagents.loader import load_subagent_configs, resolve_subagent_tools
from agent.tools.assign_skill import create_assign_skill_tool
from agent.tools.chart_generator import create_generate_chart_tool
from agent.tools.download_sandbox_file import create_download_tool
from agent.tools.hitl_tools import request_order_info
from agent.middleware_config import create_analyst_middleware, create_order_middleware
from agent.tools.web_search import web_search


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
@dataclasses.dataclass
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
        #  加载 MCP 工具
        all_mcp_tools, analyst_mcp_tools, order_mcp_tools, chart_mcp_tools = (
            await load_mcp_tools()
        )
    except  Exception:
        logger.exception("MCP 工具加载失败")
        raise RuntimeError("MCP 工具加载失败，无法预计算")

    # ---- Phase 3: 可视化工具合并（将多个图表子工具合并为一个 generate_visualization 工具）----
    logger.info("Phase 3: 合并可视化工具 (26→1)...")
    #
    generate_visualization, extra_mcp_tools = create_generate_chart_tool(chart_mcp_tools)
    if extra_mcp_tools:
        logger.info(f"  保留独立工具: {[t.name for t in extra_mcp_tools]}")

    # ---- Phase 5: 子 Agent YAML 配置加载 ----
    logger.info("Phase 5: 加载子 Agent YAML 配置...")
    #   加载原始子代理配置
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


# =============================================================================
# ★ 2. create_main_agent —— 每次请求基于 per-user 沙箱创建 agent graph
# =============================================================================

async def create_main_agent(
        config: RunnableConfig,
        *,
        sandbox_backend: SandboxBackendProtocol,
        precomputed: PrecomputedContext,
):
    """
        创建 ERP 采购智能助手的 per-request agent graph factory。

        每次请求调用，使用预计算的 MCP工具 / YAML 配置 + 外部传入的 per-user 沙箱
        轻量级创建 agent graph。SandboxBackendProxy 保证沙箱热替换不丢引用。

        Args:
            config (RunnableConfig): LangGraph RunnableConfig，含 thread_id + user_id。
            sandbox_backend (SandboxBackendProtocol): per-user 沙箱后端（SandboxBackendProxy）
            precomputed (PrecomputedContext):  启动时预计算的 MCP 工具/YAML 配置。

         Returns:
             可执行的 Agent Graph 对象（由 create_deep_agent 返回）。
    """

    user_id = config["configurable"]["user_id"]
    logger.info("=== 为用户 %s 创建 Agent Graph ===", user_id)

    # ---- Phase 1: CompositeBackend factory ----
    # 每次请求重建 CompositeBackend（StoreBackend 依赖 runtime），
    # 外部 sandbox_backend 是 SandboxBackendProxy（热替换不丢引用）。
    # routes 定义路径前缀到后端的映射：/memories/ → StoreBackend（用户级隔离），
    # /persisted-skills/ → StoreBackend（按技能命名空间组织）。
    def backend_factory(runtime):
        return CompositeBackend(
            default=sandbox_backend,
            routes={
                "/memories/": StoreBackend(
                    runtime=runtime,
                    #  按 user_id 命名空间隔离，默认值 'liqiang' 为开发环境兜底
                    namespace=lambda rt: (getattr(rt.runtime.context, 'user_id', 'liqiang'))
                ),
                "/persisted-skills/": StoreBackend(
                    runtime=runtime,
                    namespace=lambda rt: SKILLS_STORE_NAMESPACE
                )
            },
        )

    # ---- Phase 1.4: 上传 AGENTS.md 到沙箱 ----
    logger.info("Phase 1.4: 上传 AGENTS.md 到沙箱...")
    ag_md_content = LOCAL_AGENTS_MD.read_text(encoding="utf-8")
    sandbox_backend.upload_files([("/AGENTS.md", ag_md_content.encode("utf-8"))])

    # ---- Phase 2/3/5: 使用预计算结果 ----
    logger.info("Phase 2-5: 使用预计算的 MCP 工具 + 图表工具 + YAML 配置...")
    generate_visualization = precomputed.generate_visualization
    extra_mcp_tools = precomputed.extra_mcp_tools

    # ---- Phase 3.6: 创建 sandbox 依赖工具（per-request）----
    logger.info("Phase 3.6: 创建 sandbox 依赖工具...")
    # 技能分配工具
    assign_skill = create_assign_skill_tool(
        sandbox_backend,
        store=STORE,
        skills_namespace=SKILLS_STORE_NAMESPACE,
    )
    # 文件下载工具
    download_sandbox_file = create_download_tool(sandbox_backend, DOWNLOAD_DIR)

    # ---- Phase 4: 构建工具池 ----
    logger.info("Phase 4: 构建工具池...")
    # 汇总所有可用工具：分析师 MCP 工具 + 订单 MCP 工具 + 额外 MCP 工具 +
    # 可视化工具 + 网络搜索 + 下单请求 + 技能分配 + 文件下载
    available_tools = (
            list(precomputed.analyst_mcp_tools)
            + list(precomputed.order_mcp_tools)
            + list(extra_mcp_tools)
            + [generate_visualization]
            + [web_search]
            + [request_order_info]
            + [assign_skill]
            + [download_sandbox_file]
    )
    logger.info(f"  工具池: {len(available_tools)} 个工具")
    # ---- Phase 6: 子 Agent 中间件（analyst 依赖 backend_factory）----
    logger.info("Phase 6: 创建子 Agent 中间件...")
    extra_middleware = {
        #  分析师中间件
        "procurement-analyst": create_analyst_middleware(SUMMARY_MODEL, backend_factory),
        #  订单中间件
        "procurement-order": create_order_middleware(),
    }

    # ---- Phase 7: 子 Agent 工具解析 ----
    logger.info("Phase 7: 解析子 Agent 工具名称...")
    # 根据 YAML 配置将可用工具映射到子 Agent，生成子 Agent 定义列表
    subagents = resolve_subagent_tools(
        precomputed.raw_subagent_configs,
        available_tools,
        extra_middleware=extra_middleware,
    )
    logger.info(f"  已解析 {len(subagents)} 个子 Agent")

    # ---- Phase 8: 主 Agent 中间件栈 ----
    logger.info("Phase 8: 构建主 Agent 中间件栈...")
    # 中间件按顺序执行，每个中间件在 Agent 执行步骤的不同阶段介入
    main_middleware = [
        # 1. 沙箱健康守护：每次 agent step 前 ping → 失败自动恢复
        SandboxHealthMiddleware(
            sandbox_backend=sandbox_backend,
            user_id=user_id,
            agents_md_content=ag_md_content.encode("utf-8"),
        ),
        # 2. 工具错误捕获：wrap_tool_call → ToolMessage(status="error")，防止单工具崩溃
        ToolErrorMiddleware(),
        # 3. 用户上下文注入
        ContextInjectionMiddleware(),
        # 4. 技能同步（本地 → 沙箱）
        SkillsSyncMiddleware(sandbox_backend),
        # 5. 持久化技能恢复（StoreBackend → 沙箱）
        UserSkillsRestoreMiddleware(sandbox_backend, SKILLS_STORE_NAMESPACE),
        # 6. todo 对话摘要
        build_summarization_middleware(backend_factory, SUMMARY_MODEL),
        # 7. 用户记忆更新
        MemoryUpdateMiddleware(model=SUMMARY_MODEL),
        # 8. 沙箱熔断：连续沙箱错误 ≥ 阈值 → jump_to=end
        SandboxCircuitBreakerMiddleware(),
        # 9. 调用限制
        ModelCallLimitMiddleware(run_limit=50),
        ToolCallLimitMiddleware(run_limit=200),
    ]

    # ---- Phase 9: create_deep_agent ----
    logger.info("Phase 9: 创建 Deep Agent...")
    agent_graph = create_deep_agent(
        model=MAIN_MODEL,
        system_prompt=system_prompt,
        skills=["/skills/main/"],
        memory=[AGENTS_MD_FILENAME],
        tools=[web_search, assign_skill, download_sandbox_file],
        subagents=subagents,
        middleware=main_middleware,
        backend=backend_factory,
        store=STORE,
        checkpointer=CHECKPOINTER,
        context_schema=ProcurementContext,
    )

    logger.info(f"=== 用户 {user_id} Agent Graph 创建完成 ===")
    return agent_graph
