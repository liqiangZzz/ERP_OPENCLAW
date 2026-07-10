"""
零部件管理工具模块

提供零部件分页查询、名称搜索、按供应商查询等工具，注册到 MCP Server。
"""
from typing import Optional

from fastmcp import FastMCP, Context

# 分组名称，用于生成 MCP 工具名称前缀（最终工具名称为 part_query / part_search）
GROUP_NAME = "part"


# =============================================================================
# ★ 1. register_parts_tools —— 注册零部件管理工具
# =============================================================================
def register_parts_tools(mcp: FastMCP):
    """注册零部件分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_parts_tool(
            current: Optional[int] = 1,
            size: Optional[int] = 10,
            name: Optional[str] = None,
            category: Optional[str] = None,
            supplier_id: Optional[int] = None,
            ctx: Context = None,
    ) -> list:
        """
        分页查询零部件列表。
        支持按名称模糊查询、按分类筛选、按供应商ID筛选。

        Args:
            current: 当前页码，从1开始，默认1
            size: 每页大小，默认10
            name: 零件名称（模糊查询），可选
            category: 分类(发动机类/车架类/电气类/制动类/传动类/外观件)，可选
            supplier_id: 供应商ID，可选
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        #  构建请求参数 （过滤 None 值，映射到API 字段名）
        request_params = {}
        if current is not None:
            request_params["current"] = current
        if size is not None:
            request_params["size"] = size
        if name is not None:
            request_params["name"] = name
        if category is not None:
            request_params["category"] = category
        if supplier_id is not None:
            request_params["supplierId"] = supplier_id

        try:
            response = await http_client.get("/parts/page", params=request_params)
            response.raise_for_status()
            result = response.json()

            # 检查业务状态码
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            # 返回 data 字段，通常包含 records，total，current，size 等
            return result.get("data", {}).get("records", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']

    @mcp.tool(name=f"{GROUP_NAME}_search")
    async def search_parts(name: str, ctx: Context) -> list:
        """
        按名称搜索零部件。
        与 part_query 不同，此接口直接搜索，name 为必填参数。

        Args:
            name: 零件名称（模糊查询），必填
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/parts/search", params={"name": name})
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            # data 为数组
            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']

    @mcp.tool(name=f"{GROUP_NAME}_by_supplier")
    async def list_parts_by_supplier(supplier_id: int, ctx: Context) -> list:
        """
        根据供应商 ID 查询该供应商下有采购记录的零配件列表。

        Args:
            supplier_id: 供应商 ID（路径参数，必填）
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        try:
            response = await http_client.get(f"/parts/supplier/{supplier_id}")
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']
