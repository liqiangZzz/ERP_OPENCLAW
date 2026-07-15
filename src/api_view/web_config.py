# =============================================================================
# ★ 后端配置文件
# ★
# ★ 包含 MongoDB 连接配置、项目路径等
# =============================================================================
"""
后端配置文件

包含 MongoDB 连接配置、项目路径等
"""
from pathlib import Path

from agent import env_utils

# =============================================================================
# ★ 1. MongoDB 配置 —— 用于存储 Agent 的短期记忆（checkpoint）
# =============================================================================
# MongoDB 配置统一从 agent.env_utils 读取，避免 Web 与 Agent 配置漂移。
MONGODB_URI = env_utils.MONGODB_URI
MONGODB_DB_NAME = env_utils.MONGODB_DB_NAME
MONGODB_CHECKPOINT_COLLECTION = env_utils.MONGODB_CHECKPOINT_COLLECTION

# =============================================================================
# ★ 2. 项目路径配置
# =============================================================================

# 项目根目录
PROJECT_DIR = Path(__file__).parent.parent

# Agent 相关代码路径
AGENT_DIR = PROJECT_DIR / "src" / "agent"
# 技能目录（沙箱中的路径，与 sandbox_agent.py 保持一致）
SKILLS_PATH = "/workspace/skills"
# 内存文件（AGENTS.md）
MEMORY_PATH = "/AGENTS.md"
# 子代理配置文件（在 src 目录下）
SUBAGENTS_CONFIG = PROJECT_DIR / "src" / "subagents.yaml"
# AGENTS.md 文件路径
AGENTS_MD_PATH = PROJECT_DIR / "src" / "AGENTS.md"

# =============================================================================
# ★ 3. 服务配置
# ============================================================

# API 服务标题
API_TITLE = "DeepAgent Chat API"
# API 版本
API_VERSION = "1.0.0"
# API 描述
API_DESCRIPTION = "基于 DeepAgent 的 AI 对话系统 API"
