"""
库存管理工具模块

提供库存预警查询等工具，注册到 MCP Server。
"""
import httpx
from fastmcp import Context, FastMCP

from mcp_server.result_utils import ensure_nonempty_content

# 分组名称，用于生成 MCP 工具名称前缀
GROUP_NAME = "inventory"


# =============================================================================
# ★ 1. register_inventory_tools —— 注册库存管理工具
# =============================================================================
def register_inventory_tools(mcp: FastMCP):
    """注册库存管理分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_warning")
    async def list_inventory_warnings(ctx: Context):
        """
        查询库存预警列表。返回所有库存不足（当前库存低于安全库存）的物料及对应的零部件详情。无需传参。
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/inventory/warning")
            response.raise_for_status()
            result = response.json()

            # 1. 业务状态码拦截
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            # 2. 正常查无数据
            data = result.get("data", [])
            if not data:
                return ["【查询成功】当前所有物料库存充足，未发现任何库存预警。"]

            # 3. 正常有数据返回
            return ensure_nonempty_content(data)
        # 4. 异常拦截
        except httpx.TimeoutException:
            return ["【网络超时】查询库存预警请求超时，后端服务可能繁忙。请稍后重试或告知用户系统响应缓慢。"]
        except httpx.ConnectError:
            return ["【连接失败】无法连接到后端服务。请停止重试，并告知用户系统当前不可用。"]
        except Exception as e:
            return [f"【未知异常】查询库存预警时发生意外错误：{str(e)}。请停止重试。"]
