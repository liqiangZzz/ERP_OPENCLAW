"""
子 Agent YAML 配置加载器。

从 YAML 配置文件目录读取子 Agent 定义，转换为 DeepAgents
create_deep_agent() 所需的 SubAgent dict 格式。

使用方式:
    from agent.subagents.loader import load_subagent_configs, resolve_subagent_tools

    # 1. 加载原始配置（tools 为字符串名称列表）
    raw_configs = load_subagent_configs()

    # 2. 与 MCP 工具匹配，解析为可调用的工具对象
    subagents = resolve_subagent_tools(raw_configs, all_mcp_tools)

配置流程:
    YAML 文件 → load_subagent_configs() → 原始配置字典列表
    → resolve_subagent_tools() + MCP 工具 → 可传入 create_deep_agent 的 SubAgent 列表
"""
from pathlib import Path
from typing import Any, Optional, List, Dict

import yaml

# =============================================================================
# ★ 1. 全局常量：CONFIGS_DIR
# =============================================================================
# 默认 YAML 配置文件目录（与 loader.py 同级下的 configs/ 文件夹）
CONFIGS_DIR = Path(__file__).parent / "configs"


# =============================================================================
# ★ 2. 公开 API：load_subagent_configs
# =============================================================================
def load_subagent_configs(configs_dir: Optional[Path] = None, ) -> list[dict]:
    """
    加载指定目录下所有 .yaml 子 Agent 配置文件
    按文件名排序后依次读取每个 YAML 文件名，校验必填字段后返回。
    若某个文件解析失败或缺少必填字段，跳过该文件并打印错误，继续处理下一个。

    每个 YAML 文件的顶级结构：
        name: str               # 必须 — 子 Agent 唯一名称
        description: str        # 必须 — 供主 Agent 决策的描述
        system_prompt: str      # 必须 — 子 Agent 系统提示词
        tools: list[str]        # 必须 — 工具名称列表（运行时解析）
        model: str              # 可选 — 模型标识符
        skills: list[str]       # 可选 — 技能路径列表

    Args:
        configs_dir: 配置文件目录，默认为 CONFIGS_DIR。

    Returns:
        SubAgent 配置字典列表，tools 字段暂为字符串名称。
    """
    if configs_dir is None:
        configs_dir = CONFIGS_DIR

    configs: list[dict] = []

    if not configs_dir.exists():
        print(f"[WARNING] 子 Agent 配置目录不存在: {configs_dir}")
        return configs

    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

        except yaml.YAMLError as exc:
            print(f"[ERROR] 解析 YAML 文件失败: {yaml_file}")
            continue
        except Exception as e:
            print(f"[ERROR] 读取 YAML 文件失败: {yaml_file}")
            continue

        # 校验必填字段
        missing = _validate_subagent_config(data, yaml_file.name)
        if missing:
            print(f"[ERROR] 子 Agent 配置文件缺少必填字段: {yaml_file}")
            continue

        configs.append(data)
        print(f"[INFO] 已加载子 Agent 配置: {data['name']} ← {yaml_file.name}")

    return configs


# =============================================================================
# ★ 3. 公开 API：resolve_subagent_tools
# =============================================================================
def resolve_subagent_tools(
        configs: List[Dict],
        available_tools: List,
        extra_middleware: Dict[str, List] | None = None,
) -> List[Dict]:
    """
    将 YAML 配置中的工具名称字符串解析为实际的工具可调用对象。

    匹配规则：
    - YAML中 tools 的每个字段串作为前缀/子串，与 available_tools 的 .name  属性匹配
    - 例如： "supplier_query" 匹配名为 "supplier_query"的工具
    - 例如： "supplier_" 匹配所有以 "supplier_" 开头的工具

    解析过程：
    1. 构建工具名  → 工具对象的索引字典
    2. 对每个子 Agent 配置，遍历工具名称模式，执行子串匹配
    3. 去重后组装为 create_deep_agent() 可直接接收的 SubAgent 字典


  Args:
    configs: load_subagent_configs() 返回的原始配置列表。
    available_tools: 可用的工具对象列表（来自 MCP 客户端）。
    extra_middleware: 子 Agent 额外中间件，key 为子 Agent 名称，
                      value 为中间件实例列表。

    Returns:
        可以直接传入 create_deep_agent(subagents=...) 的 SubAgent 字典列表。
    """
    if extra_middleware is None:
        extra_middleware = {}

    # 构建工具名 → 工具对象的索引字典，用于 O(1) 快速查找
    # key: 工具名称, value: 工具对象
    tool_index: Dict[str, Any] = {}
    for t in available_tools:
        name = getattr(t, "name", None)
        if name:
            tool_index[name] = t

    subagents: List[Dict] = []
    for config in configs:
        tools_name = config.get("tools", [])
        resolved_tools = []

        # ── 工具匹配阶段：按模式匹配可用工具 ──
        for pattern in tools_name:
            matched = False
            # 子串匹配："supplier_query" 匹配同名工具，"supplier_" 匹配所有以此为前缀的工具
            for tool_name, tool_obj in tool_index.items():
                if pattern in tool_name:  # 子串匹配（而非前缀匹配），更灵活
                    resolved_tools.append(tool_obj)
                    matched = True
            if not matched:
                print(
                    f"[WARNING] 子 Agent '{config['name']}': "
                    f"工具模式 '{pattern}' 未能匹配任何可用工具"
                )

        # ── 去重阶段（保留顺序）：同名工具只保留第一个 ──
        seen = set()
        unique_tools = []
        for t in resolved_tools:
            name = getattr(t, "name", id(t))
            if name not in seen:
                seen.add(name)
                unique_tools.append(t)

        # ── 组装 SubAgent 字典 ──
        subagent: Dict = {
            "name": config["name"],
            # 去除 description 中的换行符，避免多行系统提示中出现额外空行
            "description": config["description"].replace("\n", "").strip(),
            "system_prompt": config["system_prompt"],
            "tools": unique_tools,
        }

        # 可选字段：模型标识符
        if config.get("model"):
            subagent["model"] = config["model"]

        # 可选字段：技能路径列表
        if config.get("skills"):
            subagent["skills"] = config["skills"]

        # 可选字段：中断条件（满足条件时暂停主 Agent，等待子 Agent 返回）
        if config.get("interrupt_on"):
            subagent["interrupt_on"] = config["interrupt_on"]

        # ── 合并中间件：YAML 配置 + extra_middleware ──
        # YAML 中的 middleware 先加载，extra_middleware 中的追加到后面
        agent_middleware = list(config.get("middleware", []))
        if config.get("name") in extra_middleware:
            agent_middleware.extend(extra_middleware[config["name"]])
        if agent_middleware:
            subagent["middleware"] = agent_middleware

        subagents.append(subagent)
        print(
            f"[INFO] 子 Agent '{config['name']}' 已解析: "
            f"{len(unique_tools)} 个工具, "
            f"{len(config.get('skills', []))} 个技能"
        )

    # 所有子 Agent 处理完毕，返回列表
    return subagents


# =============================================================================
# ★ 4. 内部函数：_validate_subagent_config
# =============================================================================
def _validate_subagent_config(data: Dict, filename: str) -> List[str]:
    """
    校验 SubAgent 配置的必填字段。

    检查以下规则:
    - name、description、system_prompt、tools 四个字段必须存在且不为 None
    - tools 必须是非空列表
    - system_prompt 必须是非空字符串

    Args:
        data: 从 YAML 解析出的配置字典。
        filename: 文件名（仅用于日志，不影响校验结果）。

    Returns:
        缺失或校验失败的字段名列表（空列表表示通过校验）。
    """
    required = ["name", "description", "system_prompt", "tools"]
    missing = [f for f in required if f not in data or data[f] is None]

    # tools 必须是非空列表
    if "tools" in data:
        tools = data["tools"]
        if not isinstance(tools, list) or len(tools) == 0:
            missing.append("tools (必须为非空列表)")

    # system_prompt 不能为空字符串
    if "system_prompt" in data:
        sp = data["system_prompt"]
        if not isinstance(sp, str) or not sp.strip():
            missing.append("system_prompt (必须为非空字符串)")

    return missing
