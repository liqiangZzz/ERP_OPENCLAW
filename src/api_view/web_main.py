# =============================================================================
# ★ DeepAgent Chat API - FastAPI 主应用
# ★
# ★ 提供基于 DeepAgent 的 AI 对话系统后端 API
# =============================================================================
"""
DeepAgent Chat API - FastAPI 主应用

提供基于 DeepAgent 的 AI 对话系统后端 API
"""
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from api_view.agent_loader import agent_loader
from api_view.api import chat, history
from api_view.web_config import API_DESCRIPTION, API_TITLE, API_VERSION

# deepagents 0.4.x 的 task 工具将 ToolRuntime 的 context 泛型保留为默认 None，
# 主 Agent 使用 ProcurementContext 时 Pydantic 会在每个流事件重复输出序列化告警。
# 运行时上下文仍可正常读取；只过滤这一条已知且范围明确的第三方告警。
warnings.filterwarnings(
    "ignore",
    message=(
        r"(?s)^Pydantic serializer warnings:.*field_name='context'.*"
        r"input_type=ProcurementContext"
    ),
    category=UserWarning,
    module=r"pydantic\.(main|functional_validators)",
)


# =============================================================================
# ★ 1. 应用生命周期管理
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    在应用启动时初始化 Agent，在应用关闭时清理资源
    """
    # ============================================================
    # 应用启动时执行
    # ============================================================
    print("=" * 50)
    print("正在启动 DeepAgent Chat API...")
    print("=" * 50)

    # 初始化 Agent
    await  agent_loader.initialize()

    print("=" * 50)
    print("DeepAgent Chat API 启动成功!")
    print("=" * 50)

    # 继续运行
    yield

    # ============================================================
    # 应用关闭时执行
    # ============================================================
    await  agent_loader.shutdown()

    print("DeepAgent Chat API 已关闭")


# =============================================================================
# ★ 2. FastAPI 应用实例
# =============================================================================

# 创建 FastAPI 应用
app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

# =============================================================================
# ★ 3. CORS 中间件配置
# =============================================================================
import os

# 允许跨域请求，从环境变量读取，默认为开发环境配置
# 生产环境应设置 ALLOWED_ORIGINS 环境变量，多个域名用逗号分隔
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "")
if _allowed_origins:
    # 生产环境：使用环境变量中配置的域名
    allow_origins = [origin.strip() for origin in _allowed_origins.split(",") if origin.strip()]
else:
    # 开发环境：允许本地开发
    allow_origins = ["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# ★ 4. 注册路由
# =============================================================================

# 对话相关接口
app.include_router(chat.router, prefix="/api", tags=["对话"])

# 历史记录相关接口
app.include_router(history.router, prefix="/api", tags=["历史记录"])


# =============================================================================
# ★ 5. 根路径
# =============================================================================

@app.get("/", tags=["首页"])
async def root():
    """
    根路径
    返回 API 基础信息
    """

    return {
        "name": API_TITLE,
        "version": API_VERSION,
        "description": API_DESCRIPTION,
        "docs": "/docs",
        "redoc": "/redoc"
    }


# =============================================================================
# ★ 6. 健康检查
# =============================================================================
@app.get("/health", tags=["系统健康检查"])
async def health():
    """
    健康检查接口

    用于检查服务是否正常运行
    """
    return {
        "status": "healthy",
        "service": API_TITLE,
        "version": API_VERSION,
    }

# =============================================================================
# ★ 7. 启动命令
# =============================================================================
# uvicorn api_view.web_main:app --reload --host 0.0.0.0 --port 8000
