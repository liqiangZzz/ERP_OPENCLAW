# =============================================================================
# ★ 后端配置文件
# ★
# ★ 包含 MongoDB 连接配置、项目路径等
# =============================================================================
"""
后端配置文件

包含 MongoDB 连接配置、项目路径等
"""
import os
from pathlib import Path

# =============================================================================
# ★ 1. MongoDB 配置 —— 用于存储 Agent 的短期记忆（checkpoint）
# =============================================================================
# 从环境变量读取 MongoDB 配置
_mongodb_host = os.getenv("MONGODB_HOST", "localhost")
_mongodb_port = int(os.getenv("MONGODB_PORT", "27017"))
_mongodb_user = os.getenv("MONGODB_USER", "")
_mongodb_password = os.getenv("MONGODB_PASSWORD", "")
_mongodb_db = os.getenv("MONGODB_DB", "langchain_db")

if _mongodb_user and _mongodb_password:
    MONGODB_URI = f"mongodb://{_mongodb_user}:{_mongodb_password}@{_mongodb_host}:{_mongodb_port}/?authSource=admin"
else:
    MONGODB_URI = f"mongodb://{_mongodb_host}:{_mongodb_port}/"

MONGODB_DB_NAME = _mongodb_db
MONGODB_CHECKPOINT_COLLECTION = "checkpoints"

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
