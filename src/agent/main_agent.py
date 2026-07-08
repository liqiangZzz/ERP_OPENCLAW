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
import logging
import os
import sys


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