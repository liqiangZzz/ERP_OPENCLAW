from mcp_server.result_utils import ensure_nonempty_content


def test_empty_containers_become_text_content() -> None:
    assert ensure_nonempty_content([]) == "[]"
    assert ensure_nonempty_content({}) == "{}"


def test_nonempty_values_are_unchanged() -> None:
    value = [{"id": 1}]

    assert ensure_nonempty_content(value) is value
