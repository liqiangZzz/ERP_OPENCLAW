"""
对话 API 模块

提供流式对话接口、中断恢复接口和会话状态查询接口
"""
import json
import os
import re
import tempfile
from datetime import datetime

from fastapi import APIRouter

# 创建路由
router = APIRouter()

# 调试日志文件路径 （在项目根目录的 temp 文件夹下）
DEBUG_LOG_DIR = os.path.join(tempfile.gettempdir(), "deepagent_debug")
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)


def get_debug_log_path(thread_id: str) -> str:
    """获取当前会话的调试日志文件路径"""
    safe_id = thread_id.replace("/", "_").replace("\\", "_")[:50]
    return os.path.join(DEBUG_LOG_DIR, f"stream_{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def get_debug_log(filepath: str, event_type: str, data: dict, raw_token: object = None):
    """
    写入调试日志（追加模式），用于记录 LLM 流式响应的中间状态，便于排查问题。

    日志格式示例：
        [2026-07-08T10:30:00.123456] on_chat_model_stream
           data: {"chunk_index": 1}
           token: {"type": "AIMessageChunk", "content_preview": "你好"}

    Args:
        filepath:   日志文件路径，如 "/tmp/debug_stream.log"
        event_type: 事件类型，如 "on_chat_model_stream"、"on_tool_start" 等
        data:       事件附加数据字典，会被 JSON 序列化后截断至 2000 字符
        raw_token:  LLM 返回的原始 token 对象（AIMessageChunk / ToolMessage 等），
                    可选；不为 None 时提取 type/name/content/tool_call_chunks/id 字段
    """
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            # ── 写入事件时间戳和类型 ──
            f.write(f"[{datetime.now().isoformat()}] {event_type} \n")
            # ── 写入事件附加数据（截断防止单行过长） ──
            f.write(f"   data: {json.dumps(data, ensure_ascii=False, default=str)[:2000]}\n")

            # ── 提取 raw_token 的关键字段 ──
            if raw_token is not None:
                token_info = {}

                # token 类型（如 AIMessageChunk、ToolMessage 等）
                if hasattr(raw_token, "type"):
                    token_info['type'] = raw_token.type

                # token 名称（工具调用时为工具名）
                if hasattr(raw_token, "name"):
                    token_info['name'] = raw_token.name

                # token 内容 —— 按类型截断，避免日志膨胀
                if hasattr(raw_token, "content"):
                    content = raw_token.content
                    if isinstance(content, str):
                        token_info['content_preview'] = content[:500]
                    elif isinstance(content, list):
                        # 列表型 content（如多模态消息），记录长度 + 预览
                        token_info["content_len"] = len(content)
                        token_info['content_preview'] = str(content)[:500]
                    else:
                        token_info['content_preview'] = str(content)[:500]

                # 工具调用分片信息（流式响应中逐步拼接的 tool_call 片段）
                if hasattr(raw_token, "tool_call_chunks") and raw_token.tool_call_chunks:
                    token_info["tool_call_chunks"] = str(raw_token.tool_call_chunks)[:500]

                # token 唯一标识
                if hasattr(raw_token, "id"):
                    token_info["id"] = raw_token.id

                f.write(f"   token:{json.dumps(token_info, ensure_ascii=False, default=str)[:2000]}\n")

            # 空行分隔不同事件
            f.write("\n")
    except Exception:
        pass  # 调试日志失败不影响主流程


def extract_subagent_name(namespace: tuple) -> str:
    """
    从 LangGraph 的 namespace 元组中提取子代理名称。

    LangGraph 多代理场景下，namespace 格式如：
        ("main", "tools:code_interpreter", "step_3")
        ("main", "tools:web_search")

    匹配以 "tools" 开头的段，去掉 "tools:" 前缀即为子代理名；
    若无 tools 段则说明是主代理自身，返回 "main"。

    Args:
        namespace: LangGraph 节点命名空间元组

    Returns:
        子代理名称，如 "code_interpreter"、"web_search"；无匹配时返回 "main"
    """
    for segment in namespace:
        if segment.startswith("tools"):
            # "tools:code_interpreter" → "code_interpreter"
            return segment.replace("tools:", "")

    return "main"


def is_likely_uuid(text: str) -> bool:
    """
    判断文本是否大概率是 UUID（而非有意义的回复内容）。

    在 LLM 流式响应中，有时模型会先输出一个消息 ID（如 run_id / message_id），
    这类 UUID 对用户没有意义，前端展示时应过滤掉。

    匹配标准 UUID v4 格式：8-4-4-4-12 位十六进制字符
    例如：3576bba4-42e5-a769-c2f7-ea8444829951

    Args:
        text: 待检测的文本字符串

    Returns:
        True 表示文本符合 UUID 格式，应视为无意义 ID；False 表示可能是正常内容
    """
    uuid_pattern = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    return bool(re.match(uuid_pattern, text.strip()))


def extract_content_from_token(token) -> str:
    """
    从 LLM 流式 token 中提取纯文本内容，供前端逐字渲染。

    LLM 返回的 token.content 类型不固定：
    - str:   普通文本流（最常见）
    - list:  结构化内容，如多模态消息 [{"type": "text", "text": "..."}, {"type": "image_url", ...}]
    - other: 兜底转字符串

    处理逻辑：
    1. 过滤 UUID 格式的无意义 ID（由 is_likely_uuid 判定）
    2. 列表型内容只提取 text 类型，忽略 image_url 等（图片由 serialize_tool_result 处理）
    3. 将各段文本拼接为单个字符串返回

    Args:
        token: LangChain 的 AIMessageChunk / ToolMessage 等 token 对象

    Returns:
        提取到的纯文本内容；无内容时返回空字符串
    """
    if not hasattr(token, "content"):
        return ""

    content = token.content

    # ── 字符串：直接返回，过滤 UUID ──
    if isinstance(content, str):
        if is_likely_uuid(content):
            return ""
        return content

    # ── 列表：逐项提取文本，忽略图片等非文本类型 ──
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                # 结构化内容：只提取 text 类型，忽略 image_url 等
                item_type = item.get("type", "")
                if item_type == "text":
                    t = item.get("text", "")
                    if not is_likely_uuid(t):
                        text_parts.append(t)
                elif item_type == "image_url":
                    # 图片 URL 不提取为文本，由 serialize_tool_result 处理
                    pass
                elif "text" in item:
                    t = item["text"]
                    if not is_likely_uuid(str(t)):
                        text_parts.append(str(t))
                elif "content" in item:
                    text_parts.append(str(item["content"]))
            else:
                text_parts.append(str(item))
        return ''.join(text_parts)

    # ── 其他类型：兜底转字符串 ──
    return str(content) if content is not None else ""


def serialize_tool_result(content) -> dict:
    """
    将工具执行结果序列化为前端可用的结构化数据（文本 + 图片）。

    工具返回的 content 类型不固定：
    - str:   纯文本（可能包含 markdown 图片语法 ![...](url)）
    - list:  结构化内容，如 [{"type": "text", ...}, {"type": "image_url", ...}]
    - other: 兜底转字符串

    返回结构：
        {
            "text": "合并后的纯文本内容",
            "images": ["https://xxx.png", "data:image/png;base64,..."]
        }

    图片提取来源（按优先级）：
    1. 结构化 image_url / image 类型项
    2. markdown 图片语法 ![alt](url)
    3. data:image base64 内嵌图片

    Args:
        content: 工具消息的 content 字段（str / list / other）

    Returns:
        包含 text 和 images 的字典，供前端分别渲染文本和图片
    """
    result = {"text": "", "images": []}

    # ── 字符串：直接作为文本，提取 markdown 图片语法 ──
    if isinstance(content, str):
        result["text"] = content
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', content):
            url = match.group(1)
            if url not in result["images"]:
                result["images"].append(url)

    # ── 列表：逐项分类提取文本和图片 ──
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    t = item.get("text", "")
                    if not is_likely_uuid(t):
                        text_parts.append(t)
                elif item_type == "image_url":
                    # OpenAI 格式图片：{"type": "image_url", "image_url": {"url": "..."}}
                    image_url = item.get("image_url", "")
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                    if url:
                        result["images"].append(url)
                elif item_type == 'image':
                    # base64 图片：{"type": "image", "data": "base64..."}
                    image_data = item.get("data", "") or item.get("image_data", "")
                    if image_data:
                        result["images"].append(image_data)
                elif "text" in item:
                    text_parts.append(item["text"])
                elif "content" in item:
                    text_parts.append(str(item["content"]))
                else:
                    text_parts.append(str(item))
            else:
                text_parts.append(str(item))
        result["text"] = ''.join(text_parts)

    # ── 其他类型：兜底转字符串 ──
    else:
        result["text"] = str(content) if content is not None else ""

    # ── 二次扫描：从合并后的文本中提取遗漏的图片 ──
    if result["text"]:
        # markdown 图片语法 ![alt](url)
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', result["text"]):
            url = match.group(1)
            if url not in result["images"]:
                result["images"].append(url)
        # data:image base64 内嵌图片
        for match in re.finditer(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', result["text"]):
            url = match.group(0)
            if url not in result["images"]:
                result["images"].append(url)

    return result


def create_sse_message(data: dict) -> str:
    """
    将数据字典封装为 SSE（Server-Sent Events）格式的消息字符串。

    SSE 协议要求每条消息格式为 "data: <payload>\\n\\n"，
    前端 EventSource 收到后会自动解析并触发 onmessage 回调。

    示例输出：
        data: {"type":"token","content":"你好"}

    Args:
        data: 要发送给前端的事件数据字典

    Returns:
        符合 SSE 协议的消息字符串
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"