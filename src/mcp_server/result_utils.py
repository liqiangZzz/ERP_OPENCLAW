"""MCP 工具返回值兼容处理。"""


def ensure_nonempty_content(value):
    """避免空容器被 MCP 编码成模型不接受的空 content 数组。"""
    if value == []:
        return "[]"
    if value == {}:
        return "{}"
    return value
