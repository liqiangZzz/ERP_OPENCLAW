"""
供应商管理工具模块

提供供应商搜索等工具，注册到 MCP Server。
"""
import httpx
from fastmcp import Context, FastMCP

from mcp_server.result_utils import ensure_nonempty_content

# 分组名称，用于生成 MCP 工具名称前缀
GROUP_NAME = "supplier"


# =============================================================================
# ★ 1. register_supplier_tools —— 注册供应商管理工具
# =============================================================================
def register_supplier_tools(mcp: FastMCP):
    """注册供应商管理分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_supplier(name: str, ctx: Context):
        """
        按名称模糊搜索供应商
        Args:
            name: 供应商名称（模糊查询），必填
        """

        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await  http_client.get("/suppliers/search", params={"name": name})
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"【业务异常】搜索供应商失败，错误码：{result.get('code')}。"]
            data = result.get("data", [])
            if not data:
                return [f"【查询成功】未找到名称包含 '{name}' 的供应商。请尝试更换关键词，或告知用户没有相关供应商。"]

            return ensure_nonempty_content(data)

        except httpx.TimeoutException:
            return ["【网络超时】搜索供应商请求超时。"]
        except httpx.ConnectError:
            return ["【连接失败】无法连接到后端服务，请停止重试。"]
        except Exception as e:
            return [f"【未知异常】搜索供应商时发生错误：{str(e)}"]
