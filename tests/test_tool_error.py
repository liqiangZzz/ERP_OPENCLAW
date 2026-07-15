from types import SimpleNamespace

from agent.middlewares.tool_error import _tool_call_id


def test_tool_call_id_from_dict() -> None:
    request = SimpleNamespace(tool_call={"id": "call-123", "name": "part_search"})

    assert _tool_call_id(request) == "call-123"


def test_tool_call_id_from_object() -> None:
    tool_call = SimpleNamespace(id="call-456", name="part_search")
    request = SimpleNamespace(tool_call=tool_call)

    assert _tool_call_id(request) == "call-456"
