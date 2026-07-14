"""
可视化生成工具（合并入口）。

将魔塔社区 MCP 的 26 个 generate_* 可视化工具合并为 1 个 generate_visualization，
通过 chart_type 参数路由到对应的底层 MCP 工具，大幅减少子 Agent 的工具列表。

非可视化工具（如 generate_spreadsheet）不会被合并，作为独立工具返回。

策略（方案 A）：
  - 工具描述仅含紧凑速查表（~800 tokens），列出 chart_type → 数据模式 → 特殊参数
  - 完整参数 schema 写入沙箱 /skills/procurement/chart_params.md
  - Agent 不确定参数时先 read_file 参考文件，确认后再调用 generate_visualization
  - 支持循环多次调用：读一次参考文件 → 多次调用不同 chart_type

使用方式:
    from agent.tools.chart_generator import create_generate_chart_tool

    generate_visualization, other_tools = create_generate_chart_tool(chart_mcp_tools)
"""

from typing import Dict, Any, List

from langchain_core.tools import tool

# =============================================================================
# ★ 1. 后缀常量 —— 定义可视化工具名的后缀匹配规则
# =============================================================================

# 有效的可视化后缀
# _chart 后缀的工具名会被缩短（如 bar_chart → bar）
_CHART_SUFFIXES = ("_chart",)

# _map / _diagram / _graph 后缀的工具名保留完整后缀作为 key（如 pin_map → pin_map）
_KEEP_SUFFIXES = ("_map", "_diagram", "_graph")




# =============================================================================
# ★ 7. 工厂函数 —— 创建 generate_visualization 合并工具
# =============================================================================


def create_generate_chart_tool(chart_mcp_tools: list) -> tuple:
    """
    工厂函数：将 26 个可视化 MCP 工具合并为 1 个 generate_visualization 入口。

    遍历所有传入的 MCP 工具，按后缀规则将可视化工具归类到 tool_map 中，
    非可视化工具（如 generate_spreadsheet）放入 other_tools 列表返回。

    工具匹配规则：
    - generate_xxx_chart → chart_type = xxx（如 bar_chart → bar）
    - generate_xxx_map → chart_type = xxx_map（如 pin_map → pin_map）
    - generate_xxx_diagram → chart_type = xxx_diagram
    - generate_xxx_graph → chart_type = xxx_graph

    Args:
        chart_mcp_tools: 从 MCP Server 加载的工具列表。

    Returns:
        (generate_visualization, other_tools) 二元组
        - generate_visualization: 合并后的可视化入口工具
        - other_tools: 未被合并且应保留的独立工具列表
    """
    tool_map: Dict[str, Any] = {}
    other_tools: List[Any] = []

    for t in chart_mcp_tools:
        name = t.name
        if not name.startswith("generate_"):
            # 不是 generate_ 前缀的工具，直接放入 other_tools
            other_tools.append(t)
            continue

        suffix = name[len("generate_"):]

        # _chart 后缀 → 去掉后缀作为 key（bar_chart → bar）
        matched = False
        for chart_suf in _CHART_SUFFIXES:
            if suffix.endswith(chart_suf) and suffix != chart_suf:
                chart_type = suffix[:-len(chart_suf)]
                tool_map[chart_type] = t
                matched = True
                break
        if matched:
            continue

        # _map / _diagram / _graph → 保留后缀作为 key（pin_map → pin_map）
        for keep_suf in _KEEP_SUFFIXES:
            if suffix.endswith(keep_suf) and suffix != keep_suf:
                chart_type = suffix
                tool_map[chart_type] = t
                matched = True
                break
        if not matched:
            # 不匹配任何已知后缀模式，归入独立工具
            other_tools.append(t)

    # 动态生成工具描述（紧凑速查表 + 参考文件提示）
    tool_description = _build_tool_description(tool_map)

    # =============================================================================
    # ★ 构建合并后的 generate_visualization 工具
    # =============================================================================
    @tool("generate_visualization")
    async def generate_visualization(
        chart_type: str,
        chart_config: dict,
    ) -> str:
        """
        统一的可视化生成入口。

        根据 chart_type 参数路由到对应的底层 MCP 工具生成图表。

        Args:
            chart_type: 图表类型（如 "bar", "line", "pie", "pin_map" 等）
            chart_config: 图表配置参数（结构因 chart_type 而异）

        Returns:
            生成的图表文件路径或错误信息。
        """
        # 根据 chart_type 查找对应的底层工具
        target_tool = tool_map.get(chart_type)
        if target_tool is None:
            available = ", ".join(sorted(tool_map.keys()))
            return f"错误：未知的 chart_type '{chart_type}'。可用: {available}"

        # 调用底层工具（tool 是 LangChain 工具对象）
        try:
            result = await target_tool.ainvoke(chart_config)
            return str(result)
        except Exception as e:
            return f"图表生成失败: {e}"

    # 返回二元组：合并工具 + 独立工具
    return generate_visualization, other_tools


# =============================================================================
# ★ 4. 内部函数：_build_tool_description
# =============================================================================
def _build_tool_description(tool_map: Dict[str, Any]) -> str:
    """
    构建 generate_visualization 工具的描述文本。

    包含紧凑速查表（chart_type → 说明），提醒 Agent 参考 /skills/procurement/chart_params.md 获取完整参数 Schema。

    Args:
        tool_map: chart_type → 工具对象的映射字典

    Returns:
        工具描述字符串
    """
    lines = [
        "统一的可视化生成工具。根据 chart_type 参数路由到对应的底层工具。",
        "",
        "## 图表类型速查表",
        "",
    ]

    # 按字母顺序排序，便于查找
    for chart_type in sorted(tool_map.keys()):
        tool = tool_map[chart_type]
        # 尝试从工具描述中提取简短说明
        desc = getattr(tool, "description", "") or ""
        # 取描述的第一行或前 50 字符
        brief = desc.split("\n")[0][:50] if desc else "无说明"
        lines.append(f"- **{chart_type}**: {brief}")

    lines.extend([
        "",
        "## 使用提示",
        "- 首次使用某 chart_type 前，建议先 `read_file('/skills/procurement/chart_params.md')` 获取参数速查",
        "- 参数 schema 格式因 chart_type 而异，详见参考文件",
    ])

    return "\n".join(lines)
