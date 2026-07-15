"""
MCP Server 配置文件

包含 Java 后端 API 地址和 MCP 服务监听配置。
"""
import os

from dotenv import load_dotenv

load_dotenv(override=False)

# =============================================================================
# ★ 1. Java 后端 API 地址（可从环境变量读取）
# =============================================================================
JAVA_API_BASE_URL = os.getenv("JAVA_API_BASE_URL", "http://localhost:8080/api")

# =============================================================================
# ★ 2. MCP 服务监听配置
# =============================================================================
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
