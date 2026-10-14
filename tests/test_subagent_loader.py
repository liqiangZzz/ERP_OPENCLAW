"""resolve_subagent_tools 工具匹配与 SubAgent 组装测试。"""

from agent.subagents.loader import resolve_subagent_tools


class FakeTool:
    """模拟 MCP 工具对象，仅含 name 属性。"""

    def __init__(self, name: str):
        self.name = name


def _config(**overrides):
    base = {
        "name": "test-agent",
        "description": "测试\n子  Agent\t描述",
        "system_prompt": "you are a test agent",
        "tools": ["supplier_query"],
    }
    base.update(overrides)
    return base


def test_exact_match():
    tools = [FakeTool("supplier_query"), FakeTool("part_query")]
    result = resolve_subagent_tools([_config()], tools)
    assert [t.name for t in result[0]["tools"]] == ["supplier_query"]


def test_prefix_match_multiple_tools():
    tools = [
        FakeTool("supplier_query"),
        FakeTool("supplier_create"),
        FakeTool("part_query"),
    ]
    result = resolve_subagent_tools([_config(tools=["supplier_"])], tools)
    names = [t.name for t in result[0]["tools"]]
    assert "supplier_query" in names
    assert "supplier_create" in names
    assert "part_query" not in names


def test_typo_no_match_returns_empty():
    # 拼写错误：既不精确命中，也不是任何工具名的前缀
    tools = [FakeTool("supplier_query")]
    result = resolve_subagent_tools([_config(tools=["supplier_quary"])], tools)
    assert result[0]["tools"] == []


def test_middle_substring_no_longer_matches():
    # 旧实现的任意子串匹配已移除：中间子串不应命中
    tools = [FakeTool("supplier_query")]
    result = resolve_subagent_tools([_config(tools=["lier_que"])], tools)
    assert result[0]["tools"] == []


def test_dedup_preserves_order():
    # "supplier_" 前缀与 "supplier_query" 精确命中同一工具，应去重
    tools = [FakeTool("supplier_query"), FakeTool("supplier_create")]
    result = resolve_subagent_tools(
        [_config(tools=["supplier_", "supplier_query"])], tools
    )
    names = [t.name for t in result[0]["tools"]]
    assert names == ["supplier_query", "supplier_create"]


def test_duplicate_tool_name_last_wins():
    first, second = FakeTool("dup_tool"), FakeTool("dup_tool")
    result = resolve_subagent_tools([_config(tools=["dup_tool"])], [first, second])
    assert result[0]["tools"] == [second]


def test_tool_without_name_is_skipped():
    tools = [object(), FakeTool("supplier_query")]
    result = resolve_subagent_tools([_config()], tools)
    assert [t.name for t in result[0]["tools"]] == ["supplier_query"]


def test_description_whitespace_collapsed():
    result = resolve_subagent_tools([_config()], [FakeTool("supplier_query")])
    assert result[0]["description"] == "测试 子 Agent 描述"


def test_optional_fields_and_middleware_merge():
    mw = object()
    config = _config(
        tools=["supplier_query"],
        model="deepseek-v4-pro",
        skills=["/skills/demo"],
        interrupt_on={"order_create": {}},
    )
    result = resolve_subagent_tools([config], [FakeTool("supplier_query")],
                                     extra_middleware={"test-agent": [mw]})
    sub = result[0]
    assert sub["model"] == "deepseek-v4-pro"
    assert sub["skills"] == ["/skills/demo"]
    assert sub["interrupt_on"] == {"order_create": {}}
    assert sub["middleware"] == [mw]
