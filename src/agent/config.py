from datetime import timedelta
from pathlib import Path

import httpx
from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.memory import InMemoryStore
from opensandbox.config import ConnectionConfigSync
from pymongo import MongoClient

from agent.env_utils import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, GLM_BASE_URL, GLM_API_KEY, QWEN_BASE_URL, QWEN_API_KEY

# =============================================================================
# ★ 1. 模型配置 —— 主模型、摘要模型、备用模型
# =============================================================================
# 主 Agent 模型
MAIN_MODEL = init_chat_model(
    "glm-5.1",
    model_provider="openai",
    temperature=1.0,
    base_url=GLM_BASE_URL,
    api_key=GLM_API_KEY,
    profile={
        "max_input_tokens": 128000,
        "max_output_tokens": 8192,
        "tool_calling": True,
        "structured_output": True,
    }
)

# ★ 摘要专用模型（摘要需要稳定输出，temperature 设为较低值）
SUMMARY_MODEL = ChatOpenAI(
    model="DeepSeek-V4-Flash",
    temperature=0.3,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=256000,  # 256k tokens，上下文窗口的 2 倍
    model_kwargs={
        "extra_body": {
            "thinking": {"type": "disabled"}
        }
    }
)

# ★ 备用模型（当主模型故障时使用）
FALLBACK_MODEL = init_chat_model(
    "Qwen3.6-27B",
    model_provider="openai",
    temperature=1.0,
    base_url=QWEN_BASE_URL,
    api_key=QWEN_API_KEY,
    profile={
        "max_input_tokens": 128000,
        "max_output_tokens": 8192,
        "tool_calling": True,
        "structured_output": True,
    }
)

# =============================================================================
# ★ 2. 沙箱配置 —— OpenSandbox 连接配置
# =============================================================================
SANDBOX_CONFIG = ConnectionConfigSync(
    domain="http://192.168.0.188:8080",
    use_server_proxy=True,
    request_timeout=timedelta(seconds=60),
    transport=httpx.HTTPTransport(limits=httpx.Limits(max_connections=20)),
)

# =============================================================================
# ★ 3. 路径常量 —— 项目路径、沙箱路径、本地路径
# =============================================================================
# ★ 项目 src 根目录（所有本地路径常量的基路径）
EXAMPLE_DIR = Path(__file__).resolve().parent.parent
# ★ 沙箱内技能根路径
SANDBOX_SKILLS_ROOT = "/skills"
# ★ 沙箱内记忆根路径（用户私有记忆存放处）
SANDBOX_MEMORIES_ROOT = "/memories"
# ★ 沙箱内分析中间文件存放目录
SANDBOX_ANALYSIS_ROOT = "/analysis"
# ★ 沙箱内数据文件存放目录
SANDBOX_DATA_ROOT = "/data"
# ★ 本地技能资源目录（项目内的路径，相对于项目根）
LOCAL_SKILLS_DIR = EXAMPLE_DIR / "skills"
# ★ 本地下载目录（从沙箱下载文件的目标路径）
DOWNLOAD_DIR = EXAMPLE_DIR / "download"
# ★ 本地子 Agent 配置目录
LOCAL_SUBAGENT_CONFIG_DIR = EXAMPLE_DIR / "agent/subagents"
# ★ 本地的Agent记忆文件
LOCAL_AGENTS_MD = EXAMPLE_DIR / "agent/memory/AGENTS.md"


# =============================================================================
# ★ 4. 文件名常量 —— 沙箱内文件名、用户偏好文件名
# =============================================================================

# ★ 主 Agent 只读指引文件（上传到沙箱 /AGENTS.md）
AGENTS_MD_FILENAME = "/AGENTS.md"
# ★ 用户偏好文件名（在 /memories/{user_id}/ 下）
USER_PREFERENCES_FILENAME = "preferences.md"

# =============================================================================
# ★ 5. 用户技能持久化 —— 技能路径映射、子Agent名称映射
# =============================================================================

# ★ 技能持久化 StoreBackend 路由路径
PERSISTED_SKILLS_ROOT = "/persisted-skills"
# ★ 技能 StoreBackend 命名空间（按 Agent scope 组织，无用户隔离）
SKILLS_STORE_NAMESPACE = ("skills",)
# ★ 子 Agent 名称 → 技能 scope 目录映射
SCOPE_MAP = {
    "main": "main",
    "procurement-analyst": "procurement",
    "procurement-order": "order",
}


# =============================================================================
# ★ 6. MongoDB 配置 —— 用于持久化 Agent 短期记忆/checkpoint
# =============================================================================
MONGODB_URI = "mongodb://root:root@localhost:27017/?authSource=admin"
MONGODB_DB_NAME = "langchain_db"
MONGODB_CHECKPOINT_COLLECTION = "checkpoints"

# =============================================================================
# ★ 7. 持久化存储 —— InMemoryStore（开发）+ MongoDBSaver（生产）
# =============================================================================
# ★ InMemoryStore: 开发阶段使用。生产环境替换为持久 Store。
STORE = InMemoryStore()

# ★ MongoDBSaver: Agent 对话状态的 MongoDB 持久化 checkpointer。
# 支持 Human-in-the-Loop（interrupt 状态持久化）和跨重启对话恢复。
# ★ MongoDB 客户端实例（模块级私有变量，不对外导出）
_mongodb_client = MongoClient(MONGODB_URI)
CHECKPOINTER = MongoDBSaver(
    client=_mongodb_client,
    db_name=MONGODB_DB_NAME,
    checkpoint_collection_name=MONGODB_CHECKPOINT_COLLECTION,
)
