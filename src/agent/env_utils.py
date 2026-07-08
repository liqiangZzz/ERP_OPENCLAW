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
# ★ 1. 加载 .env 文件 —— override=True 覆盖已存在的环境变量
# =============================================================================

load_dotenv(override=True)


# =============================================================================
# ★ 2. 各 AI 服务商的 API Key
# =============================================================================

DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
GLM_API_KEY = os.getenv('GLM_API_KEY')
QWEN_API_KEY = os.getenv('QWEN_API_KEY')


# =============================================================================
# ★ 3. 各 AI 服务商的 API Base URL
# =============================================================================

# ---- 各 AI 服务商的 API Base URL ----
GLM_BASE_URL = os.getenv('GLM_BASE_URL')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL')
QWEN_BASE_URL = os.getenv('QWEN_BASE_URL')


# =============================================================================
# ★ 4. 本地服务和沙箱相关配置
# =============================================================================

LOCAL_BASE_URL = os.getenv('LOCAL_BASE_URL')
DAYTONA_API_KEY = os.getenv('DAYTONA_API_KEY')
DAYTONA_BASE_URL = os.getenv('DAYTONA_BASE_URL')
