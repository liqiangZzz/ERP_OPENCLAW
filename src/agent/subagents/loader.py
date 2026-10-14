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

工具匹配规则：精确匹配优先（O(1) 索引），未命中再前缀匹配
（如 "supplier_" 匹配所有 supplier_ 前缀的工具），均失败记 WARNING。
刻意不支持任意子串匹配——子串匹配会把 YAML 中的工具名拼写错误
静默"容错"掉，配置错误被吞掉后很难排查。
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 模块级 logger，与 main_agent 的日志体系统一
logger = logging.getLogger(__name__)

# 默认 YAML 配置目录（与 loader.py 同级的 configs/）
# 每个业务子 Agent 一个 .yaml 文件，按文件名排序加载保证注册顺序稳定
CONFIGS_DIR = Path(__file__).parent / "configs"


# =============================================================================
# ★ 1. 公开 API：load_subagent_configs
# =============================================================================
def load_subagent_configs(configs_dir: Optional[Path] = None) -> List[Dict]:
    """
    加载目录下所有 .yaml 子 Agent 配置，按文件名排序。

    容错策略：单个文件解析失败或缺少必填字段时跳过并记日志，
    不让一个坏文件阻断整个 Agent 启动。

    YAML 顶级结构：
        name: str               # 必须 — 子 Agent 唯一名称
        description: str        # 必须 — 供主 Agent 决策的描述
        system_prompt: str      # 必须 — 子 Agent 系统提示词
        tools: list[str]        # 必须 — 工具名称列表（运行时解析）
        model: str              # 可选 — 模型标识符，不配置则继承主 Agent
        skills: list[str]       # 可选 — 技能路径列表

    Returns:
        SubAgent 配置字典列表，tools 字段暂为字符串名称。
    """
    if configs_dir is None:
        configs_dir = CONFIGS_DIR

    configs: List[Dict] = []

    # 目录不存在属于部署问题，返回空列表交由调用方决定是否告警
    if not configs_dir.exists():
        logger.warning("子 Agent 配置目录不存在: %s", configs_dir)
        return configs

    for yaml_file in sorted(configs_dir.glob("*.yaml")):
        # YAML 语法错误 / IO 错误分开捕获，日志更精确
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error("解析 YAML 文件失败: %s — %s", yaml_file, e)
            continue
        except Exception as e:
            logger.error("读取文件失败: %s — %s", yaml_file, e)
            continue

        # 必填字段缺失则跳过该文件，不进入后续解析
        missing = _validate_subagent_config(data, yaml_file.name)
        if missing:
            logger.error("%s 缺少必填字段: %s", yaml_file.name, ", ".join(missing))
            continue

        configs.append(data)
        logger.info("已加载子 Agent 配置: %s ← %s", data["name"], yaml_file.name)

    return configs


# =============================================================================
# ★ 2. 公开 API：resolve_subagent_tools
# =============================================================================
def resolve_subagent_tools(
        configs: List[Dict],
        available_tools: List,
        extra_middleware: Dict[str, List] | None = None,
) -> List[Dict]:
    """
    将 YAML 配置中的工具名称字符串解析为实际的工具对象，组装为 SubAgent 列表。

    分三步（对应下方三个内部函数）：
        1. _build_tool_index : 工具列表 → 名称索引字典（只建一次，全程复用）
        2. _match_tools     : 按模式解析工具，去重保序
        3. _build_subagent  : 原始配置 + 已解析工具 → SubAgent 字典

    匹配规则（逐级降级）：
    1. 精确匹配：模式与工具名完全相同（O(1) 索引查找）
    2. 前缀匹配：模式作为工具名前缀，如 "supplier_"；降级匹配记 INFO
    3. 无匹配：记 WARNING（常见于工具名拼写错误）

    Args:
        configs: load_subagent_configs() 返回的原始配置列表。
        available_tools: 可用的工具对象列表（来自 MCP 客户端）。
        extra_middleware: 子 Agent 额外中间件，key 为子 Agent 名称，
                          value 为中间件实例列表。YAML 只能写出字符串/
                          字典、表达不了实例，所以中间件只能从这里代码注入。

    Returns:
        可直接传入 create_deep_agent(subagents=...) 的 SubAgent 字典列表。
    """
    if extra_middleware is None:
        extra_middleware = {}

    # 工具索引只建一次，所有子 Agent 共享
    tool_index = _build_tool_index(available_tools)

    subagents: List[Dict] = []
    for config in configs:
        tools = _match_tools(config.get("tools", []), tool_index, config["name"])
        subagents.append(_build_subagent(config, tools, extra_middleware))
        logger.info(
            "子 Agent '%s' 已解析: %d 个工具, %d 个技能",
            config["name"], len(tools), len(config.get("skills", [])),
        )

    return subagents


# =============================================================================
# ★ 3. 内部函数：_build_tool_index
# =============================================================================
def _build_tool_index(available_tools: List) -> Dict[str, Any]:
    """
    构建工具名 → 工具对象的索引字典，供精确匹配 O(1) 查找。

    - 无 name 属性的工具直接跳过
    - 同名工具后者覆盖前者（与 FastMCP 注册行为一致），记 warning
    """
    tool_index: Dict[str, Any] = {}
    for t in available_tools:
        name = getattr(t, "name", None)
        if not name:
            continue
        if name in tool_index:
            logger.warning("工具名 '%s' 重复，后注册者覆盖前者", name)
        tool_index[name] = t
    return tool_index


# =============================================================================
# ★ 4. 内部函数：_match_tools（精确 → 前缀）
# =============================================================================
def _match_tools(patterns: List[str], tool_index: Dict[str, Any], agent_name: str) -> List:
    """
    按模式列表解析工具，返回去重后的工具对象列表（保持声明顺序）。

    去重要保序：工具顺序影响 LLM 看到的工具列表顺序（越靠前越容易被
    选用），按 YAML 声明顺序保留首次出现的工具，与配置意图一致。

    Args:
        patterns: YAML 配置中的工具名称模式列表。
        tool_index: 工具名 → 工具对象的索引字典。
        agent_name: 子 Agent 名称（仅用于日志）。
    """
    resolved: List = []

    for pattern in patterns:
        # 第 1 级：精确匹配（最高频路径，O(1) 查找不遍历）
        if pattern in tool_index:
            resolved.append(tool_index[pattern])
            continue

        # 第 2 级：前缀匹配（降级路径，记 INFO 便于排查配置意图）
        # 典型场景："supplier_" 一次授权所有 supplier_ 前缀的工具
        prefix_matched = [
            obj for name, obj in tool_index.items()
            if name.startswith(pattern)
        ]
        if prefix_matched:
            logger.info(
                "子 Agent '%s': 模式 '%s' 前缀匹配到 %s",
                agent_name, pattern,
                [getattr(obj, "name", "?") for obj in prefix_matched],
            )
            resolved.extend(prefix_matched)
            continue

        # 第 3 级：无匹配，大概率是 YAML 中工具名拼写错误
        logger.warning(
            "子 Agent '%s': 工具模式 '%s' 未能匹配任何可用工具",
            agent_name, pattern,
        )

    # 去重（保序）：如 "supplier_"（前缀）与 "supplier_query"（精确）
    # 同时出现时同一工具会被解析两次，这里只保留第一个
    seen = set()
    unique_tools = []
    for t in resolved:
        name = getattr(t, "name", None) or id(t)
        if name not in seen:
            seen.add(name)
            unique_tools.append(t)

    return unique_tools


# =============================================================================
# ★ 5. 内部函数：_build_subagent
# =============================================================================
def _build_subagent(config: Dict, tools: List, extra_middleware: Dict[str, List]) -> Dict:
    """
    将单条原始配置 + 已解析工具组装为 SubAgent 字典。

    可选字段（model / skills / interrupt_on / middleware）未配置时不写入，
    避免空值覆盖 DeepAgents 框架的默认行为。
    """
    subagent: Dict = {
        "name": config["name"],
        # 折叠所有空白字符为单个空格：description 会被拼进主 Agent 的
        # 系统提示，多行文本会产生额外空行/缩进，干扰模型理解
        "description": " ".join(config["description"].split()),
        "system_prompt": config["system_prompt"],
        "tools": tools,
    }

    # 模型标识符，不配置则继承主 Agent 模型
    if config.get("model"):
        subagent["model"] = config["model"]

    # 技能路径列表
    if config.get("skills"):
        subagent["skills"] = config["skills"]

    # 中断条件（Human-in-the-Loop）：如 order_create / order_update
    # 执行前暂停主 Agent，等待人工审批
    if config.get("interrupt_on"):
        subagent["interrupt_on"] = config["interrupt_on"]

    # 合并中间件：YAML 中的先加载，extra_middleware 追加到后面
    # （洋葱模型：后追加的在外层，可对前者做前置/后置处理）
    agent_middleware = list(config.get("middleware", []))
    if config["name"] in extra_middleware:
        agent_middleware.extend(extra_middleware[config["name"]])
    if agent_middleware:
        subagent["middleware"] = agent_middleware

    return subagent


# =============================================================================
# ★ 6. 内部函数：_validate_subagent_config
# =============================================================================
def _validate_subagent_config(data: Dict, filename: str) -> List[str]:
    """
    校验必填字段：name / description / system_prompt / tools。

    - tools 必须是非空列表（空列表意味着子 Agent 无任何能力）
    - system_prompt 必须是非空字符串

    Returns:
        缺失或非法的字段名列表（空列表表示通过校验）。
    """
    required = ["name", "description", "system_prompt", "tools"]
    missing = [f for f in required if f not in data or data[f] is None]

    if "tools" in data:
        tools = data["tools"]
        if not isinstance(tools, list) or len(tools) == 0:
            missing.append("tools (必须为非空列表)")

    if "system_prompt" in data:
        sp = data["system_prompt"]
        if not isinstance(sp, str) or not sp.strip():
            missing.append("system_prompt (必须为非空字符串)")

    return missing
