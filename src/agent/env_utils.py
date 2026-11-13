# =============================================================================
# ★ 环境变量工具模块 —— 通过 dotenv 加载 .env，提供 API Key / Base URL 全局访问
# =============================================================================
"""
环境变量工具模块。

通过 dotenv 加载 .env 配置文件，提供各 AI 服务商 API Key 和 Base URL 的
全局访问入口。模块导入时自动执行 load_dotenv 加载环境变量。
"""
import os

from dotenv import load_dotenv

# =============================================================================
# ★ 1. 加载 .env 文件 —— 不覆盖进程环境，便于容器和生产环境注入配置
# =============================================================================

load_dotenv(override=False)


# =============================================================================
# ★ 2. 各 AI 服务商的 API Key
# =============================================================================

DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
GLM_API_KEY = os.getenv('GLM_API_KEY')
ZHIPU_API_KEY = os.getenv('ZHIPU_API_KEY')

# =============================================================================
# ★ 3. 各 AI 服务商的 API Base URL
# =============================================================================

# ---- 各 AI 服务商的 API Base URL ----
GLM_BASE_URL = os.getenv('GLM_BASE_URL')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL')

# =============================================================================
# ★ 4. 本地服务和沙箱相关配置
# =============================================================================

LOCAL_BASE_URL = os.getenv('LOCAL_BASE_URL')
DAYTONA_API_KEY = os.getenv('DAYTONA_API_KEY')
DAYTONA_BASE_URL = os.getenv('DAYTONA_BASE_URL')

# 内网的 OpenSandbox 服务的 API Key
SANDBOX_DOMAIN = os.getenv("SANDBOX_DOMAIN", "http://localhost:8081")
OPENSANDBOX_API_KEY = os.getenv("OPENSANDBOX_API_KEY")
PREWARM_SANDBOX = os.getenv("PREWARM_SANDBOX", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CLEANUP_SANDBOX_ON_SHUTDOWN = os.getenv(
    "CLEANUP_SANDBOX_ON_SHUTDOWN",
    "false",
).lower() in {"1", "true", "yes", "on"}

# 数据库与服务地址
MONGODB_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://root:root@localhost:27017/?authSource=admin",
)
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "langchain_db")
MONGODB_CHECKPOINT_COLLECTION = os.getenv(
    "MONGODB_CHECKPOINT_COLLECTION",
    "checkpoints",
)

ERP_MCP_URL = os.getenv("ERP_MCP_URL", "http://127.0.0.1:8000/mcp")

# 分析服务 MCP 地址
ANALYSIS_MCP_URL = os.getenv("ANALYSIS_MCP_URL")

# Store 持久化后端：mongo（默认，重启不丢）/ memory（InMemoryStore，仅测试用）
STORE_BACKEND = os.getenv("STORE_BACKEND", "mongo").lower()
