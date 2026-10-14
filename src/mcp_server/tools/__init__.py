"""
MCP Server 业务工具集

各模块均通过一个 register_xxx_tools(mcp) 函数注册工具：内部利用闭包
（在函数内定义工具函数并用 @mcp.tool 装饰）将工具挂到 FastMCP 实例上，
由 server_main.py 统一调用。

工具都是拿 lifespan 里的 http_client 转发后端 ERP API，code != 200 或
异常时直接返回错误信息，不抛出。

- inventory_tools : inventory_warning 库存预警查询
- order_tools    : order_create / order_update / order_search_details 采购订单增改查
- parts_tools    : part_query / part_search / part_by_supplier 零部件查询
- suppliers_tools: supplier_query 供应商模糊搜索
"""
