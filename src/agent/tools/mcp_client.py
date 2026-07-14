"""
MCP 工具客户端。

在 Agent 启动时连接所有 MCP Server，获取全部 MCP 工具，
并按分组筛选后分配给不同的子 Agent。

使用方式:
    from agent.tools.mcp_client import load_mcp_tools

    all_tools, analyst_tools, order_tools, chart_tools = await load_mcp_tools()
"""
from typing import Tuple

from langchain_mcp_adapters.client import MultiServerMCPClient

# =============================================================================
# ★ 1. MCP Server 连接配置 —— 定义所有 MCP Server 的 URL 和传输协议
# =============================================================================

# MCP Server 连接配置
MCP_SERVER_CONFIG = {
    # ERP 业务 API：提供供应商、零部件、库存、订单等ERP相关工具
    "erp-api": {
        "url": "http://127.0.0.1:8000/mcp",
        "transport": "streamable_http",
    },
    # 魔塔社区分析 API： 提供可视化和图表生成工具
    "analysis": {
        "url": "https://mcp.api-inference.modelscope.net/af3893df5be041/mcp",
        "transport": "streamable_http",
    }
}

# =============================================================================
# ★ 2. 工具分组规则（前缀匹配） —— 定义分析/订单/图表工具的前缀筛选
# =============================================================================

# 工具分组规则（前缀匹配）
# 分析类工具前缀：supplier_（供应商查询）、part_（零部件查询）、inventory_（库存预警）
ANALYST_TOOL_PREFIXES = ("supplier_", "part_", "inventory_")

# 订单类工具前缀：order_（订单创建/更新/搜索）
ORDER_TOOL_PREFIXES = ("order_",)

# 图表类工具前缀：generate_（魔塔社区 MCP 可视化工具，26种图表/地图 + 1个spreadsheet）
CHART_TOOL_PREFIXES = ("generate_",)


# =============================================================================
# ★ 3. 核心函数 —— 加载 MCP 工具并分组
# =============================================================================
async def load_mcp_tools(server_config: dict | None = None) -> Tuple[list, list, list, list]:
    """
    连接到所有 MCP Server ，加载全部工具并分组。

    Args：
        server_config (dict | None): MCP Server 连接配置，默认使用 MCP_SERVER_CONFIG。

    Returns:
        (all_tools, analyst_tools, order_tools, chart_tools) 四元组
        - all_tools: 全部 MCP 工具列表（ERP + 图表）
        - analyst_tools: 供应商查询 + 零部件查询 + 库存预警工具
        - order_tools: 订单创建 + 订单更新 + 订单搜索工具
        - chart_tools: 图表/地图/可视化生成工具（来自魔塔社区 MCP Server，27 种）
    """
    if server_config is None:
        server_config = MCP_SERVER_CONFIG

    print("[INFO] 正在连接 MCP Server...")
    # 创建多服务器 MCP 客户端，同时连接所有配置的 Server
    mcp_client = MultiServerMCPClient(server_config)

    # 从 ERP MCP Server 获取业务工具
    erp_tools = await mcp_client.get_tools(server_name="erp-api")
    print(f"[INFO] 已从ERP MCP Server 加载 {len(erp_tools)} 个业务工具")

    # 从魔塔社区 MCP Server 获取图表工具
    analysis_tools = await mcp_client.get_tools(server_name="analysis")
    print(f"[INFO] 已从魔塔社区 MCP Server 加载 {len(analysis_tools)} 个图表工具（可视化+其他）")

    # 合并全部工具
    all_tools = list(erp_tools) + list(analysis_tools)

    # 按前缀分组： 分析类业务分组
    analyst_tools = [
        t for t in erp_tools
        if t.name.startswith(ANALYST_TOOL_PREFIXES)
    ]

    # 按前缀分组： 订单类业务分组
    order_tools = [
        t for t in erp_tools
        if t.name.startswith(ORDER_TOOL_PREFIXES)
    ]

    # 按前缀分组： 图表类业务分组,图表工具（来自魔塔社区）
    chart_tools = [
        t for t in analysis_tools
        if t.name.startswith(CHART_TOOL_PREFIXES)
    ]

    print(
        f"[INFO] 工具分组完成: "
        f"分析类 {len(analyst_tools)} 个, "
        f"订单类 {len(order_tools)} 个, "
        f"图表类 {len(chart_tools)} 个"
    )

    return all_tools, analyst_tools, order_tools, chart_tools