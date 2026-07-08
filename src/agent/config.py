from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI

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
    model="deepseek-v4-flash",
    temperature=0.3,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=2560000,
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