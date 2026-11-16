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

from deepagents import AsyncSubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.runnables import RunnableConfig

from agent.config import SKILLS_STORE_NAMESPACE, LOCAL_AGENTS_MD, STORE, DOWNLOAD_DIR, SUMMARY_MODEL, MAIN_MODEL, \
    AGENTS_MD_FILENAME, CHECKPOINTER
from agent.memory.prompts import system_prompt
from agent.middleware_config import create_analyst_middleware, create_order_middleware
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
from agent.tools.mcp_client import load_mcp_tools
from agent.tools.web_search import web_search

# 异步采购分析任务图 ID
ASYNC_ANALYST_GRAPH_ID = "procurement_analyst_async"
# 异步采购分析任务图 URL
ASYNC_ANALYST_URL = os.environ.get(
    "ASYNC_AGENT_PROTOCOL_URL",
    "http://127.0.0.1:2024",
)

# 异步采购分析任务图指令
ASYNC_ANALYST_INSTRUCTIONS = """
## 异步采购分析任务

当用户提出耗时的采购分析、供应商比价、行情调研、成本评估、报告生成或需要多步数据收集的任务时，
必须使用 `start_async_task` 启动 `procurement-analyst` 后台任务。

严禁使用同步 `task` 工具启动 `procurement-analyst`。`task` 工具只用于 `procurement-order` 等同步订单任务。

启动任务后必须立即把完整 task_id 返回给用户，并停止本轮回答。不要立刻调用 `check_async_task`。
只有用户明确询问进度、结果、取消或补充要求时，才调用 `check_async_task`、`list_async_tasks`、
`cancel_async_task` 或 `update_async_task`。
"""


def _setup_logging() -> None:
    """根据 APP_ENV 环境变量初始化全局日志配置。

    生产环境写入文件（erp_agent.log），级别 INFO；
    开发环境输出到标准输出，级别 WARNING（不用 ERROR，否则中间件 except 吞掉的 warning 全被过滤，bug 静默隐形）。
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
            level=logging.WARNING,
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
    Phase 0.1-0.3 预计算结果的不可变容器（启动跑一次；每请求的装配阶段见 create_main_agent 的 Phase 1-9）。

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
    Phase 0.1-0.3 预计算，启动时执行一次，所有请求复用。

     加载 MCP 工具、合并可视化工具、加载子 Agent YAML 配置。
    这些操作不依赖用户沙箱，结果可在所有请求间共享。

    Returns:
        PrecomputedContext: 包含所有预计算组件的数据类。

    Raises:
        RuntimeError: 当 MCP 工具加载失败时抛出。
    """

    logger.info("=== 预计算 Agent 上下文（Phase 0.1-0.3）===")

    # ---- Phase 0.1: MCP 工具加载 ----
    logger.info("Phase 0.1: 加载 MCP 工具...")
    try:
        #  加载 MCP 工具
        all_mcp_tools, analyst_mcp_tools, order_mcp_tools, chart_mcp_tools = (
            await load_mcp_tools()
        )
    except  Exception:
        logger.exception("MCP 工具加载失败")
        raise RuntimeError("MCP 工具加载失败，无法预计算")

    # ---- Phase 0.2: 可视化工具合并（将多个图表子工具合并为一个 generate_visualization 工具）----
    logger.info("Phase 0.2: 合并可视化工具 (26→1)...")

    # 将魔塔MCP中的图表子工具合并为一个 generate_visualization 工具
    generate_visualization, extra_mcp_tools = create_generate_chart_tool(chart_mcp_tools)

    # 保留独立的图表子工具，extra_mcp_tools 是非可视化工具，不是 generate_ 前缀的工具，直接放入 other_tools ，不参与合并
    if extra_mcp_tools:
        logger.info(f"  保留独立工具: {[t.name for t in extra_mcp_tools]}")

    # ---- Phase 0.3: 子 Agent YAML 配置加载 ----
    logger.info("Phase 0.3: 加载子 Agent YAML 配置...")

    #   加载原始子代理配置
    raw_configs = load_subagent_configs()
    if not raw_configs:
        logger.warning("  未找到任何子 Agent 配置")
    else:
        logger.info(f"  已加载 {len(raw_configs)} 个子 Agent 配置")

    logger.info("=== 预计算完成 ===")
    return PrecomputedContext(
        all_mcp_tools=all_mcp_tools,  # 所有 MCP 工具，包括可视化工具
        analyst_mcp_tools=analyst_mcp_tools,  # 分析师 MCP 工具
        order_mcp_tools=order_mcp_tools,  # 订单 MCP 工具
        chart_mcp_tools=chart_mcp_tools,  # 图表 MCP 工具
        extra_mcp_tools=extra_mcp_tools,  # 非可视化工具
        generate_visualization=generate_visualization,  # 图表生成器
        raw_subagent_configs=raw_configs,  # 原始子代理配置列表
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
        创建 ERP 采购智能助手的 per-request agent graph factory（装配阶段 Phase 1-9）。

        每次请求调用，使用预计算的 MCP工具 / YAML 配置 + 外部传入的 per-user 沙箱
        轻量级创建 agent graph。SandboxBackendProxy 保证沙箱热替换不丢引用。

        Args:
            config (RunnableConfig): LangGraph RunnableConfig，含 thread_id + user_id。
            sandbox_backend (SandboxBackendProtocol): per-user 沙箱后端（SandboxBackendProxy）
            precomputed (PrecomputedContext):  启动时预计算的 MCP 工具/YAML 配置。

         Returns:
             可执行的 Agent Graph 对象（由 create_deep_agent 返回）。
    """

    # =========================================================================
    # ★ 核心架构设计（Graph Factory 模式）：
    # 1. 无状态计算（每次重建）：每次请求都会在内存中极速组装一个全新的 Graph，
    #    彻底杜绝多用户并发时的状态污染与数据越权。
    # 2. 记忆不丢（状态外置）：Graph 本身不持有对话历史，所有记忆通过 Thread ID
    #    自动从外部的 CHECKPOINTER（如 Redis）中恢复，实现逻辑计算与业务状态分离。
    # 3. 故障无感（代理热替换）：传入的 sandbox_backend 是一个代理（Proxy），
    #    即使底层沙箱崩溃重建，Agent Graph 持有的引用依然有效，实现无感热恢复。
    # =========================================================================

    user_id = config["configurable"]["user_id"]
    logger.info("=== 为用户 %s 创建 Agent Graph ===", user_id)

    # ---- Phase 1: CompositeBackend 实例 ----
    # 【核心路由与解耦】
    # - routes 定义虚拟文件系统的前缀路由：
    #   /memories/ 走 StoreBackend（按用户隔离）；
    #   /persisted-skills/ 走 StoreBackend（按技能命名空间组织）。
    # - default=sandbox_backend 是代理对象（Proxy），即使底层沙箱热替换，引用也不会丢失。
    #
    # 【框架底层机制与版本适配】
    # - deepagents 0.7 起移除 backend 工厂，必须直接传初始化好的 BackendProtocol 实例。
    # - StoreBackend 运行期会自动从 LangGraph 执行上下文解析 runtime，
    #   因此 namespace 只需传入一个闭包（lambda），无需接收 runtime 形参。
    backend = CompositeBackend(
        default=sandbox_backend,  # 默认沙箱后端（临时文件、代码执行等操作）
        routes={
            "/memories/": StoreBackend(
                # 按 user_id 命名空间隔离，与 MemoryUpdateMiddleware 的 (user_id,) 保持一致；
                # user_id 来自 config["configurable"]，按 user_id 隔离。
                # 这里必须用 lambda 包装（闭包），而不能直接传变量。
                # 原因：Agent 是并发处理请求的。用 lambda 生成闭包，可以把当前请求的
                # user_id 牢牢锁在函数内部，确保多用户同时请求时，数据绝不会串线。
                # _rt 是底层自动传入的 runtime 参数，这里用不到，故用下划线忽略。
                namespace=lambda _rt: (user_id,),
                store=STORE,
            ),
            "/persisted-skills/": StoreBackend(
                namespace=lambda _rt: SKILLS_STORE_NAMESPACE,# 持久化技能不区分用户，按全局技能命名空间组织
                store=STORE,
            ),
        },
    )

    # ---- Phase 2: 上传 AGENTS.md 到沙箱 ----
    logger.info("Phase 2: 上传 AGENTS.md 到沙箱...")
    ag_md_content = LOCAL_AGENTS_MD.read_text(encoding="utf-8")
    sandbox_backend.upload_files([("/AGENTS.md", ag_md_content.encode("utf-8"))])

    # ---- Phase 3: 复用预计算结果（Phase 0.1-0.3 的产物）----
    logger.info("Phase 3: 复用预计算的 MCP 工具 + 图表工具 + YAML 配置...")
    generate_visualization = precomputed.generate_visualization
    extra_mcp_tools = precomputed.extra_mcp_tools

    # ---- Phase 4: 创建 sandbox 依赖工具（per-request）----
    logger.info("Phase 4: 创建 sandbox 依赖工具...")
    # 技能分配工具
    assign_skill = create_assign_skill_tool(
        sandbox_backend,
        store=STORE,
        skills_namespace=SKILLS_STORE_NAMESPACE,
    )
    # 文件下载工具
    download_sandbox_file = create_download_tool(sandbox_backend, DOWNLOAD_DIR)

    # ---- Phase 5: 构建工具池 ----
    logger.info("Phase 5: 构建工具池...")

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

    # ---- Phase 6: 子 Agent 中间件（analyst 依赖 backend）----
    logger.info("Phase 6: 创建子 Agent 中间件...")
    extra_middleware = {
        #  分析师中间件
        "procurement-analyst": create_analyst_middleware(SUMMARY_MODEL, backend),
        #  订单中间件
        "procurement-order": create_order_middleware(),
    }

    # ---- Phase 7: 子 Agent 工具解析 ----
    logger.info("Phase 7: 解析子 Agent 工具名称...")

    # 根据 YAML 配置将可用工具映射到子 Agent，生成子 Agent 定义列表

    #  同步子 Agent 配置（不包含采购分析）
    sync_subagent_configs = [
        c for c in precomputed.raw_subagent_configs
        if c.get("name") != "procurement-analyst"
    ]

    #  同步子 Agent 工具解析，根据 YAML 配置和工具池（不包含采购分析）
    sync_subagents = resolve_subagent_tools(
        sync_subagent_configs,
        available_tools,
        extra_middleware=extra_middleware,
    )

    #  异步子 Agent 配置（采购分析），异步运行
    async_subagents: list[AsyncSubAgent] = [
        {
            "name": "procurement-analyst",
            "description": (
                "耗时采购分析专家。负责供应商比价、物料行情分析、采购策略建议、"
                "市场调研、成本评估和报告生成。后台运行，适合长任务。"
            ),
            "graph_id": ASYNC_ANALYST_GRAPH_ID,
            "url": ASYNC_ANALYST_URL,
        }
    ]

    subagents = sync_subagents + async_subagents
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
        # 6. 对话摘要
        build_summarization_middleware(backend, SUMMARY_MODEL),
        # 7. 用户记忆更新
        MemoryUpdateMiddleware(model=SUMMARY_MODEL),
        # 8. 沙箱熔断：连续沙箱错误 ≥ 阈值 → jump_to=end
        SandboxCircuitBreakerMiddleware(),
        # 9. 模型调用限制
        ModelCallLimitMiddleware(run_limit=50),
        # 10. 工具调用限制
        ToolCallLimitMiddleware(run_limit=200),
    ]

    # ---- Phase 9: create_deep_agent ----
    logger.info("Phase 9: 创建 Deep Agent...")
    agent_graph = create_deep_agent(
        model=MAIN_MODEL,
        system_prompt=system_prompt + "\n\n" + ASYNC_ANALYST_INSTRUCTIONS,
        skills=["/skills/main/"],
        memory=[AGENTS_MD_FILENAME],
        tools=[web_search, assign_skill, download_sandbox_file], # 主 Agent 对外只暴露 3 个工具，其余全部下沉给子 Agent
        subagents=subagents, #  子 Agent 配置（包含异步和同步子 Agent）
        middleware=main_middleware,
        backend=backend,
        store=STORE,
        # checkpointer 是维持多轮对话记忆的“数据库锚点”。
        # 每次请求虽然新建了 Graph，但底层会自动依据 config 里的 thread_id
        # 从这里读取并恢复历史上下文。
        checkpointer=CHECKPOINTER,
        # ============================================================
        # context_schema=ProcurementContext 的核心作用总结：
        # 1. 强类型契约与开发体验：声明后，IDE 能完美推断出 runtime.context
        #    是 ProcurementContext 对象，提供 ctx.user_id 等属性补全，避免拼写错误。
        # 2. 数据安全与防篡改：Context 是贯穿整个请求生命周期的“只读环境变量”。
        #    它与 State（对话消息历史）严格隔离，大模型无法通过生成幻觉来篡改用户身份。
        # 3. 全链路无感穿透：不仅是主 Agent 的中间件，底层的 Tools、子 Agent
        #    在执行时也能通过底层注入的 Runtime 安全地读取到当前用户是谁。
        # ============================================================
        context_schema=ProcurementContext,
    )

    logger.info(f"=== 用户 {user_id} Agent Graph 创建完成 ===")
    return agent_graph
