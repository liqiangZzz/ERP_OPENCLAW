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

from typing import Dict, Any, List, Optional, Tuple

from langchain_core.tools import tool

# =============================================================================
# ★ 1. 后缀常量 —— 定义可视化工具名的后缀匹配规则
# =============================================================================

# 有效的可视化后缀
# _chart 后缀的工具名会被缩短（如 bar_chart → bar）
_CHART_SUFFIXES = ("_chart",)

# _map / _diagram / _graph 后缀的工具名保留完整后缀作为 key（如 pin_map → pin_map）
_KEEP_SUFFIXES = ("_map", "_diagram", "_graph")

# chart_type → 使用场景映射（供 system_prompt 引用，共 26 种）
CHART_TYPE_GUIDE: Dict[str, str] = {
    # === 标准图表（19 种）===
    "area": "数据随时间变化趋势，如库存量变化",
    "bar": "供应商价格横向分类对比",
    "boxplot": "多类别数据分布对比（箱线图），如供应商交货周期分布",
    "column": "各物料采购量/金额纵向对比",
    "dual_axes": "价格+数量双轴复合分析（柱+线）",
    "funnel": "转化漏斗，如采购流程各环节转化率",
    "histogram": "价格/交货周期数值分布频率",
    "line": "价格/订单量时间趋势",
    "liquid": "单一百分比指标（水波图），如采购完成率、预算执行率",
    "organization": "组织架构层级关系",
    "pie": "采购金额按分类/供应商占比",
    "radar": "供应商多维度综合评分（价格、质量、交期、服务）",
    "sankey": "数据流向/转化（桑基图），如物料从供应商到仓库流转",
    "scatter": "价格-质量二维相关性分析",
    "treemap": "采购金额层级占比（矩形树图）",
    "venn": "集合交集关系（韦恩图），如供应商与物料交叉分析",
    "violin": "数据分布密度（小提琴图），比箱线图展示更详细分布形态",
    "waterfall": "累计增减变化（瀑布图），如采购成本逐项构成",
    "word_cloud": "物料名称/供应商高频分析词云",
    # === 地图（3 种）===
    "district_map": "中国行政区划数据分布，如各省/市采购额分布",
    "path_map": "路线规划展示，如物流配送路线",
    "pin_map": "兴趣点位置分布，如供应商/仓库地理位置",
    # === 图表/关系图（3 种）===
    "fishbone_diagram": "质量问题根因分析（鱼骨图）",
    "flow_diagram": "采购审批/业务流程步骤展示",
    "network_graph": "供应商-物料关系网络拓扑",
    # === 思维导图（1 种）===
    "mind_map": "采购策略/产品架构结构化梳理",
}

# ---- 紧凑速查表：chart_type → 数据模式 + 特殊参数（用于工具描述，~800 tokens） ----

_COMPACT_TABLE = """
## Chart Type Quick Reference

**通用可选参数（几乎所有图表）:** `width`(默认600), `height`(默认400), `title`, `theme`("default"|"academy"|"dark"), `style`({backgroundColor, palette, texture})

### category-value 模式
| chart_type | data: [{...}] | 特有可选参数 |
|-----------|------|---------|
| bar | `{category, value}` | group(bool), stack(bool) |
| column | `{category, value}` | group(bool), stack(bool) |
| pie | `{category, value}` | innerRadius(0-1, 环形图) |
| funnel | `{category, value}` | — |
| treemap | `{name, value, children?}` | — |
| word_cloud | `{text, value}` | — |

### time-value 模式
| chart_type | data: [{...}] | 特有可选参数 |
|-----------|------|---------|
| line | `{time, value, group?}` | style.lineWidth |
| area | `{time, value, group?}` | stack(bool) |

### 分布/统计模式
| chart_type | data: [{...}] | 特有可选参数 |
|-----------|------|---------|
| boxplot | `{category, value, group?}` | style.startAtZero |
| violin | `{category, value, group?}` | style.startAtZero |
| histogram | `[number, ...]` (纯数字数组) | binNumber(int) |
| scatter | `{x, y, group?}` | — |

### 多维度/流向/集合
| chart_type | data: [{...}] | 特有可选参数 |
|-----------|------|---------|
| radar | `{name, value, group?}` | style.lineWidth |
| sankey | `{source, target, value}` | nodeAlign("left"&#124;"right"&#124;"center"&#124;"justify") |
| venn | `{value, sets: [str], label?}` | — |
| waterfall | `{category, value?, isIntermediateTotal?, isTotal?}` | style.palette({positiveColor, negativeColor, totalColor}) |
| dual_axes | 见下方说明 | — |

### 特殊图表（非 data 驱动或特殊结构）
| chart_type | 核心参数 | 说明 |
|-----------|---------|------|
| liquid | **`percent`**(0-1, 必填) | 不是 data! shape: "circle"\|"rect"\|"pin"\|"triangle" |
| organization | `data: {name, description?, children: [{name, description?, children: [...]}]}` | 层级树, orient: "vertical"\|"horizontal" |
| mind_map | `data: {name, children: [{name, children: [...]}]}` | 层级树, 最大深度3 |
| fishbone_diagram | `data: {name, children: [{name, children: [...]}]}` | 层级树, 最大深度3 |
| flow_diagram | `data: {nodes: [{name}], edges: [{source, target, name?}]}` | 节点+边 |
| network_graph | `data: {nodes: [{name}], edges: [{source, target, name?}]}` | 节点+边 |

### dual_axes 特殊格式
```
chart_config = {
  "categories": ["2015", "2016", "2017"],
  "series": [
    {"type": "column", "data": [91.9, 99.1, 101.6], "axisYTitle": "销售额"},
    {"type": "line", "data": [0.055, 0.06, 0.062], "axisYTitle": "利润率"}
  ]
}
// 注意: line 的 data 建议 < 1 (表示比率)
```

### 地图
| chart_type | 核心参数 | 说明 |
|-----------|---------|------|
| district_map | **`title`**(必填) + `data: {name, dataType?, dataLabel?, dataValue?, dataValueUnit?, subdistricts?, showAllSubdistricts?}` | 中国行政区划, 见参考文件 |
| pin_map | **`title`**(必填) + `data: [str, ...]` | POI 名称数组, 可选 markerPopup |
| path_map | **`title`**(必填) + `data: [{data: [str, ...]}]` | 路线数组, 每组是一条路线 |

> **不确定 chart_config 的具体字段和格式时，先 `read_file("/skills/procurement/chart_params.md")` 查看完整参数说明和示例。**
""".strip()


# ---- 从 MCP 工具 args_schema 提取参数信息 ----

def _extract_schema(tool) -> Optional[dict]:
    """从工具提取 JSON Schema。"""
    if not hasattr(tool, "args_schema") or not tool.args_schema:
        return None
    try:
        return tool.args_schema.schema()
    except Exception:
        return None


def _prop_to_md(name: str, prop: dict, required: set, indent: int = 0) -> str:
    """将单个 JSON Schema 属性转为 Markdown 行。"""
    prefix = "  " * indent
    req = " **必填**" if name in required else ""

    # 处理嵌套对象
    if prop.get("type") == "object" and "properties" in prop:
        lines = [f"{prefix}- **`{name}`** (object){req}: {prop.get('description', '')}"]
        sub_props = prop["properties"]
        sub_req = set(prop.get("required", []))
        for sub_name, sub_prop in sub_props.items():
            lines.append(_prop_to_md(sub_name, sub_prop, sub_req, indent + 1))
        return "\n".join(lines)

    # 处理数组（含 items 为对象的情况）
    if prop.get("type") == "array" and "items" in prop:
        items = prop["items"]
        desc = prop.get("description", "")
        lines = [f"{prefix}- **`{name}`** (array){req}: {desc}"]

        if isinstance(items, dict) and items.get("type") == "object" and "properties" in items:
            item_props = items["properties"]
            item_req = set(items.get("required", []))
            for item_name, item_prop in item_props.items():
                lines.append(_prop_to_md(item_name, item_prop, item_req, indent + 1))
        return "\n".join(lines)

    # 简单类型
    ptype = prop.get("type", "any")
    if prop.get("enum"):
        ptype = " | ".join(repr(v) for v in prop["enum"])
    desc = prop.get("description", "")
    return f"{prefix}- **`{name}`** ({ptype}){req}: {desc}"


def _extract_tool_params(tool) -> str:
    """从单个 MCP 工具的 args_schema 提取参数描述。"""
    schema = _extract_schema(tool)
    if not schema:
        return "  *参数信息不可用*"
    props = schema.get("properties", {})
    if not props:
        return "  *无额外参数*"
    required = set(schema.get("required", []))
    lines = []
    for name, prop in props.items():
        lines.append(_prop_to_md(name, prop, required))
    return "\n".join(lines)


def build_chart_params_md(tool_map: Dict[str, Any]) -> str:
    """
    根据 tool_map 生成完整的图表参数参考文档（Markdown 格式），
    用于上传到沙箱 /skills/procurement/chart_params.md。

    Args:
        tool_map: chart_type → MCP StructuredTool 的映射字典

    Returns:
        Markdown 格式的完整参数参考文档
    """
    lines = [
        "# 可视化图表参数参考",
        "",
        "本文档列出 `generate_visualization` 的所有 `chart_type` 及其 `chart_config` 参数 schema。",
        "数据由魔塔社区 MCP Server 的 26 个可视化工具提供。",
        "",
        "---",
        "",
        "## 通用可选参数（几乎所有图表都支持）",
        "",
        "- **`width`** (number): 图表宽度，默认 600",
        "- **`height`** (number): 图表高度，默认 400",
        "- **`title`** (string): 图表标题",
        "- **`theme`** (string): 主题，默认 'default'，可选 'academy'、'dark'",
        "- **`style`** (object): 样式配置，通常含 `backgroundColor`、`palette`(颜色数组)、`texture`('default'|'rough'手绘风格)",
        "- **`axisXTitle`** (string): X 轴标题（柱/线/散点等有轴图表）",
        "- **`axisYTitle`** (string): Y 轴标题",
        "",
        "---",
    ]

    # 按数据模式分组
    groups: List[Tuple[str, List[str]]] = [
        ("category-value 模式", ["bar", "column", "pie", "funnel", "treemap", "word_cloud"]),
        ("time-value 模式", ["line", "area"]),
        ("分布/统计模式", ["boxplot", "violin", "histogram", "scatter"]),
        ("多维度/流向/集合", ["radar", "sankey", "venn", "waterfall", "dual_axes"]),
        ("层级树模式", ["organization", "mind_map", "fishbone_diagram"]),
        ("节点-边图模式", ["flow_diagram", "network_graph"]),
        ("特殊图表", ["liquid"]),
        ("地图", ["district_map", "pin_map", "path_map"]),
    ]

    for group_title, chart_types in groups:
        lines.append(f"## {group_title}")
        lines.append("")
        for ct in chart_types:
            t = tool_map.get(ct)
            if not t:
                continue
            usage = CHART_TYPE_GUIDE.get(ct, "")
            params_text = _extract_tool_params(t)
            lines.append(f"### {ct} — {usage}")
            lines.append("")
            lines.append(params_text)
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _build_tool_description(tool_map: Dict[str, Any]) -> str:
    """生成 generate_visualization 的工具描述（紧凑速查表 + 参考文件提示）。"""
    chart_list = ", ".join(sorted(tool_map.keys()))
    return (
        "Generate a data visualization and return the image URL.\n"
        "\n"
        "Use this tool to create charts, maps, diagrams, or mind maps for analysis reports.\n"
        f"Available chart_type values: {chart_list}\n"
        "\n"
        + _COMPACT_TABLE +
        "\n\n"
        "When unsure about the exact fields for chart_config, read "
        "`/skills/procurement/chart_params.md` first, then call this tool."
    )


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
        chart_tool = tool_map.get(chart_type)
        if not chart_tool:
            available = ", ".join(sorted(tool_map.keys()))
            return f"错误：未知的 chart_type '{chart_type}'。可用: {available}"

        # 调用底层工具（tool 是 LangChain 工具对象）
        result = await chart_tool.ainvoke(chart_config)
        return result

    # 注入动态生成的描述
    generate_visualization.name = "generate_visualization"
    generate_visualization.description = tool_description

    # 返回二元组：合并工具 + 独立工具
    return generate_visualization, other_tools
