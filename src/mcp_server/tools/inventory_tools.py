"""
库存管理工具模块

提供库存预警查询等工具，注册到 MCP Server。
"""
from fastmcp import FastMCP, Context

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
           查询库存预警列表。
           返回所有库存不足 （当前库存低于安全库存） 的无聊及对应的零部件详情。

           无需传参
        """

        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/inventory/warning")
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']
