"""
对话 API 模块

提供流式对话接口、中断恢复接口和会话状态查询接口
"""
import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from idlelib.undo import Command
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from agent.scheam import ChatRequest, Message, ChatResponse
from api_view.agent_loader import agent_loader

# 创建路由
router = APIRouter()

# 调试日志文件路径 （在项目根目录的 temp 文件夹下）
DEBUG_LOG_DIR = os.path.join(tempfile.gettempdir(), "deepagent_debug")
os.makedirs(DEBUG_LOG_DIR, exist_ok=True)


def get_debug_log_path(thread_id: str) -> str:
    """获取当前会话的调试日志文件路径"""
    safe_id = thread_id.replace("/", "_").replace("\\", "_")[:50]
    return os.path.join(DEBUG_LOG_DIR, f"stream_{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def write_debug_log(filepath: str, event_type: str, data: dict, raw_token: object = None):
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


# ============================================================
# 中断恢复请求模型
# ============================================================
class ResumeRequest(BaseModel):
    """
    中断回复请求体，resume 子弹的格式取决于中断类型：
     - 数据补充中断 {"supplement":"用户自由文本输入"}
     -- HITL 审批中断 {"decisions":[{"type":"approve"}]}   或 [{"type": "reject"}]
    """
    resume: dict
    user_id: str = Field(None, description="用户ID")


# ============================================================
# 流式对话核心逻辑
# ============================================================

async def stream_chat_response(
        message: str = None,
        thread_id: str = None,
        resume_data: dict = None,
        user_id: str = None,
) -> AsyncIterator[str]:
    """
    流式生成对话响应，支持 Human-in-the-Loop 中断与恢复。

    【整体流程】
        用户发送消息 → 构建 input → 调用 agent_graph.astream() 流式执行
        → 遍历 chunk（token/工具/中断）→ 实时推送 SSE 给前端 → 保存到 MongoDB

    【两种调用模式】
        1. 初始对话 - 传入 message（用户消息）
           input: {"messages": [{"role": "user", "content": message}]}
        2. 中断恢复 - 传入 resume_data（Command.resume 的值）
           input: Command(resume=resume_data)

    【chunk 类型处理】（按代码顺序）
        1. value 流 → 检测 interrupts��中断）→ 发送 interrupt 事件 → 结束流
        2. messages 流 → token → 发送到前端（打字机效果）
        3. messages 流 → tool_call → 发送 tool_start / tool_args
        4. messages 流 → tool_result → 发送 tool_result / tool_end

    【数据流示意】
        前端 ──message──▶  API  ──▶  Agent Graph  ──▶  流式返回 chunk
                                     │                │
                                     │           ┌────┴────┐
                                     │          token  tool  interrupt
                                     │           │     │       │
                                     │           ▼     ▼       ▼
                                     │        SSE 消息推送给前端
                                     │
                                     ▼
                                MongoDB（保存对话历史）

    【中断类型】
        - order_info_supplement: 数据补充中断（request_order_info 工具触发）
        - hitl_approval: HITL 审批中断（order_create|update 等需要人工确认）

    Args:
        message: 用户输��的文本消息
        thread_id: 对话线程 ID
        resume_data: 中断恢复信息
        user_id: 用户 ID

    Returns:
        异步生成器，逐字生成对话响应内容（SSE 格式）
    """

    # =============================================================================
    # ★ 1. 初始化准备
    # =============================================================================
    context = {"user_id": user_id, "username": user_id}
    config = agent_loader.create_config(thread_id, user_id=user_id)

    # 获取该用户的 per-user agent graph （沙箱缓存 + 预计算组件）
    agent_graph = await agent_loader.get_agent_for_user(user_id)

    collected_content = ""

    # 工具调用栈，支持嵌套工具调用（主代理调用 task → 子代理调 generate_chart）
    tool_call_stack = []

    # =============================================================================
    # ★ 2. 根据模式构建 input 和 display_messages
    # =============================================================================
    # 两种调用模式：
    #   - 初始对话 (message 有值): 新建展示消息，用普通消息作为 input
    #   - 中断恢复 (resume_data 有值): 加载已有展示消息，用 Command(resume=...)恢复
    if resume_data is not None:
        # 恢复模式： 加载已有展示消息，用 Command(resume=...)恢复
        existing = await agent_loader.get_display_messages(thread_id) or []
        display_messages = existing
        current_input = Command(resume=resume_data)
    else:
        # 初始模式： 新建展示消息，用普通消息作为 input
        # 参数校验：message 不能为空
        if not message or not message.strip():
            raise ValueError("message 不能为空")

        current_input = {"messages": [{
            "role": "user",
            "content": message
        }]}
        display_messages = [
            {
                "id": f"user-{uuid.uuid4()}",
                "role": "user",
                "content": message
            }
        ]

    # 创建调试日志文件
    debug_log = get_debug_log_path(thread_id)

    def _last_display_is_assistant():
        return (display_messages
                and display_messages[-1]["role"] == "assistant")

    try:
        write_debug_log(debug_log, "STREAM_START", {
            "message": message,
            "thread_id": thread_id,
            "is_resume": resume_data is not None,
        })

        # =============================================================================
        # ★ 3. 流式调用 agent.astream()
        # =============================================================================
        # stream_mode 说明：
        #   - "messages": 流式输出 token/工具等消息内容
        #   - "values":    流式输出图状态变化（用于检测中断）
        # subgraphs=True: 启用子代理流式输出（让子代理的 token 也能实时推送）
        async for chunk in agent_graph.astream(
                input=current_input,
                config=config,
                context=context,
                stream_mode=["messages", "values"], # messages=消息流, values=状态流
                subgraphs=True,  # 启用子代理流式输出
                version="v2",
        ):
            chunk_type = chunk.get("type")

            # =============================================================================
            # ★ 3.1 中断检测（value 流，必须在 messages 处理之前）
            # =============================================================================
            # 中断类型：
            #   - order_info_supplement: 数据补充中断（request_order_info 工具触发）
            #   - hitl_approval: HITL 审批中断（interrupt_on 配置）
            if chunk_type == "value" and chunk.get("interrupts"):
                interrupts = chunk.get("interrupts")
                write_debug_log(debug_log, "INTERRUPT_DETECTED", {
                    "count": len(interrupts),
                })

                for interrupt in interrupts:
                    interrupt_value = interrupt.get("value")

                    if "action_requests" in interrupt_value:
                        # ---- 第 2 层：HITL 审批中断（interrupt_on 配置）----
                        yield create_sse_message({
                            "type": "interrupt",
                            "interrupt_type": "hitl_approval",
                            "action_requests": interrupt_value["action_requests"],
                            "review_config": interrupt_value.get("review_config", []),
                            "thread_id": thread_id,
                        })
                        write_debug_log(debug_log, "INTERRUPT_HITL", {
                            "actions": [a["name"] for a in interrupt_value["action_requests"]],
                        })

                    elif interrupt_value.get("type") == "order_info_request":
                        # ---- 第 1 层：数据补充中断（request_order_info 工具）----
                        yield create_sse_message({
                            "type": "interrupt",
                            "interrupt_type": "order_info_supplement",
                            "missing_fields": interrupt_value["missing_fields"],
                            "collected_data": interrupt_value["collected_data"],
                            "thread_id": thread_id,
                        })
                        write_debug_log(debug_log, "INTERRUPT_SUPPLEMENT", {
                            "missing_fields": interrupt_value["missing_fields"],
                        })

                    else:
                        # 未知中断类型，透传原始值
                        yield create_sse_message({
                            "type": "interrupt",
                            "interrupt_type": "unknown",
                            "interrupt_value": str(interrupt_value)[:2000],
                            "thread_id": thread_id,
                        })

                # 兜底： 将所有calling 状态的工具标记为 done
                for dm in display_messages:
                    if dm["role"] == "tool" and dm["tool_status"] == "calling":
                        dm["tool_status"] = "done"

                # 清理空的 assistant 消息
                cleaned = [
                    dm for dm in display_messages
                    if not (dm["role"] == "assistant" and not dm.get("content"))
                ]

                # 保存部分展示消息到 MongoDB（中断前的消息状态）
                # 注意：resume 模式下 display_messages 已含历史，保存时会覆盖旧记录
                await agent_loader.save_display_messages(thread_id, cleaned)
                write_debug_log(debug_log, "SAVE_DISPLAY_INTERRUPT", {
                    "thread_id": thread_id,
                    "message_count": len(cleaned),
                })

                # 发送 done 事件标记流结束（前端由此知道可以展示中断 UI）
                yield create_sse_message({
                    "type": "done",
                    "thread_id": thread_id,
                    "content": collected_content,
                    "interrupted": True,
                })
                return  # ← 结束本次流，等待前端 POST /resume

            # =============================================================================
            # ★ 3.2 消息流处理（token / tool_call / tool_result）
            # =============================================================================
            # 跳过非消息类型的 chunk（如 checkpoint 等）
            if chunk_type != "messages":
                continue

            # 解析 token 和 namespace（判断是主代理还是子代理）
            token, metadata = chunk["data"]
            namespace = chunk.get("ns", ())

            # 判断消息来源：主代理 "main" 或子代理 "tools:xxx"
            is_subagent = any(s.startswith("tools:") for s in namespace)
            source = extract_subagent_name(namespace) if is_subagent else "main"

            write_debug_log(debug_log, "RAW_CHUNK", {
                "ns": list(namespace),
                "source": source,
            }, raw_token=token)

            # =============================================================================
            # ★ 3.2.1 工具调用（tool_call_chunks）
            # =============================================================================
            # 工具调用栈 tool_call_stack 支持嵌套调用（主代理 → 子代理 → 孙子代理）
            # 用工具名 + 栈深度生成稳定 ID，避免 tool_start 和 tool_result 的 id 不匹配
            if hasattr(token, 'tool_call_chunks') and token.tool_call_chunks:
                for tool_chunk in token.tool_call_chunks:
                    # 工具开始调用
                    tool_name = tool_chunk.get('name')
                    if tool_name:
                        # 用工具名 + 栈深度生成稳定 ID（避免每次都生成新 UUID）
                        stack_depth = len(tool_call_stack)
                        tool_id = f"{tool_name}-{stack_depth}-{hash(source) % 10000}"

                        # 检查是否已存在（避免重复添加）
                        exists = any(t.get("id") == tool_id for t in tool_call_stack)
                        if not exists:
                            new_tool = {
                                "id": tool_id,
                                "name": tool_name,
                                "args": "",
                                "source": source
                            }
                            tool_call_stack.append(new_tool)

                            yield create_sse_message({
                                "type": "tool_start",
                                "tool_call_id": tool_id,
                                "tool_name": tool_name,
                                "source": source
                            })

                            # 添加到展示消息
                            display_messages.append({
                                "id": tool_id,
                                "role": "tool",
                                "tool_name": tool_name,
                                "args": "",
                                "text": "",
                                "images": [],
                                "source": source,
                                "tool_status": "calling"
                            })

                            write_debug_log(debug_log, "TOOL_START", {
                                "name": tool_name,
                                "source": source,
                                "tool_id": tool_id,
                                "stack_depth": stack_depth
                            })

                    # 工具参数 - 追加到栈顶工具（支持 JSON 解析合并）
                    tool_args = tool_chunk.get('args')
                    if tool_args and tool_call_stack:
                        try:
                            # 尝试解析为 JSON 合并（避免字符串拼接破坏 JSON 格式）
                            import json as json_module
                            existing_args = tool_call_stack[-1]["args"]
                            if existing_args:
                                existing_json = json_module.loads(existing_args)
                                new_json = json_module.loads(tool_args)
                                existing_json.update(new_json)
                                tool_call_stack[-1]["args"] = json_module.dumps(existing_json, ensure_ascii=False)
                            else:
                                tool_call_stack[-1]["args"] = tool_args
                        except (json_module.JSONDecodeError, TypeError):
                            # 非 JSON 格式，直接拼接
                            tool_call_stack[-1]["args"] += tool_args

                        yield create_sse_message({
                            "type": "tool_args",
                            "args": tool_args,
                            "source": source
                        })

                        # 更新展示消息中对应工具的 args（从后向前找最后一个 calling 的 tool）
                        # 使用与 tool_call_stack 相同的 JSON 合并逻辑
                        for dm in reversed(display_messages):
                            if dm["role"] == "tool" and dm["tool_status"] == "calling":
                                try:
                                    import json as json_module
                                    existing_args = dm.get("args", "")
                                    if existing_args:
                                        existing_json = json_module.loads(existing_args)
                                        new_json = json_module.loads(tool_args)
                                        existing_json.update(new_json)
                                        dm["args"] = json_module.dumps(existing_json, ensure_ascii=False)
                                    else:
                                        dm["args"] = tool_args
                                except (json_module.JSONDecodeError, TypeError):
                                    dm["args"] = dm.get("args", "") + tool_args
                                break

            # =============================================================================
            # ★ 3.2.2 工具执行结果（type == "tool"）
            # =============================================================================
            # 从工具调用栈中弹出对应工具（栈顶即为当前完成的工具）
            if hasattr(token, 'type') and token.type == "tool":
                tool_name = getattr(token, 'name', '未知工具')
                result_content = getattr(token, 'content', '')

                # 序列化工具结果为结构化数据
                serialized = serialize_tool_result(result_content)

                # 从栈中弹出对应工具（栈顶即为当前完成的工具）
                finished_tool = tool_call_stack.pop() if tool_call_stack else None
                tool_id = finished_tool["id"] if finished_tool else ""

                yield create_sse_message({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "tool_call_id": tool_id,
                    "text": serialized["text"],
                    "images": serialized["images"],
                    "source": source
                })

                # 更新展示消息中对应工具的结果
                for dm in reversed(display_messages):
                    if dm["role"] == "tool" and dm["id"] == tool_id:
                        dm["text"] = serialized["text"]
                        dm["images"] = serialized["images"]
                        dm["tool_status"] = "done"
                        break

                write_debug_log(debug_log, "TOOL_RESULT", {
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "text_preview": serialized["text"][:300],
                    "images_count": len(serialized["images"]),
                    "source": source,
                    "stack_remaining": len(tool_call_stack)
                })

                # 发送 tool_end 事件，标记工具调用结束
                yield create_sse_message({
                    "type": "tool_end",
                    "tool_name": tool_name,
                    "tool_call_id": tool_id,
                    "source": source
                })

                # 确保展示消息中的工具标记为 done（兜底）
                for dm in reversed(display_messages):
                    if dm["role"] == "tool" and dm["id"] == tool_id and dm["tool_status"] == "calling":
                        dm["tool_status"] = "done"
                        break

            # =============================================================================
            # ★ 3.2.3 AI 文本内容（token）
            # =============================================================================
            # 注意：ToolMessage（type == "tool"）的内容是工具执行结果，已在上面
            # tool_result 事件中处理。这里必须跳过，否则工具结果会被当作 AI 文本重复输出。
            content_text = extract_content_from_token(token)
            has_tool_calls = hasattr(token, 'tool_call_chunks') and token.tool_call_chunks
            is_tool_result = hasattr(token, 'type') and token.type == "tool"

            if content_text and not has_tool_calls and not is_tool_result:
                collected_content += content_text
                yield create_sse_message({
                    "type": "token",
                    "content": content_text,
                    "source": source
                })
                write_debug_log(debug_log, "TOKEN", {
                    "content": content_text[:200],
                    "source": source
                })

                # 更新展示消息：如果最后一条是 assistant 且 source 相同则追加，否则新建
                # source 变化时（如 main → tools:code_interpreter → main）需要新建消息
                if _last_display_is_assistant() and display_messages[-1].get("source") == source:
                    display_messages[-1]["content"] += content_text
                else:
                    display_messages.append({
                        "id": f"assistant-{uuid.uuid4()}",
                        "role": "assistant",
                        "content": content_text,
                        "source": source
                    })

        # =============================================================================
        # ★ 4. 流正常结束（无中断）- 保存到 MongoDB
        # =============================================================================
        #   - 兜底：将所有 calling 状态的工具标记为 done
        #   - 清理空的 assistant 消息
        #   - 多轮对话：追加到已有历史，而非覆盖
        #   - 发送 done 事件
        # ---- 流正常结束（无中断）----
        # 兜底：将所有 calling 状态的工具标记为 done
        for dm in display_messages:
            if dm["role"] == "tool" and dm["tool_status"] == "calling":
                dm["tool_status"] = "done"

        # 清理空的 assistant 消息
        display_messages = [
            dm for dm in display_messages
            if not (dm["role"] == "assistant" and not dm.get("content"))
        ]

        # 保存完整展示消息到 MongoDB（包含子代理消息）
        # 多轮对话：追加到已有消息，而非覆盖（每轮调用 save 时 display_messages 仅含当前轮）
        if resume_data is None:
            # 初始对话：追加到已有历史
            existing = await agent_loader.get_display_messages(thread_id) or []
            all_messages = existing + display_messages
        else:
            # resume 模式：display_messages 已包含历史（load 时获取），直接保存
            all_messages = display_messages
        await agent_loader.save_display_messages(thread_id, all_messages)
        write_debug_log(debug_log, "SAVE_DISPLAY", {
            "thread_id": thread_id,
            "total_count": len(all_messages)
        })

        # 流结束，发送 done 事件
        write_debug_log(debug_log, "STREAM_DONE", {
            "thread_id": thread_id,
            "total_content_len": len(collected_content)
        })

        yield create_sse_message({
            "type": "done",
            "thread_id": thread_id,
            "content": collected_content
        })

    # =============================================================================
    # ★ 5. 异常处理
    # =============================================================================
    except Exception as e:
        # 记录错误日志，并发送 error 事件给前端
        write_debug_log(debug_log, "STREAM_ERROR", {"error": str(e)})
        yield create_sse_message({
            "type": "error",
            "message": str(e)
        })


# ============================================================
# API 端点
# ============================================================

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    流式对话接口

    接收用户消息，返回 SSE 流式响应
    支持实时显示 AI 生成的内容和工具调用信息
    检测到 Human-in-the-Loop 中断时会发送 interrupt 事件
    """

    thread_id = request.thread_id or str(uuid.uuid4())
    user_id = request.user_id
    message = request.message

    return StreamingResponse(
        stream_chat_response(message=message, thread_id=thread_id, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/chat/{thread_id}/resume")
async def chat_resume(thread_id: str, request: ResumeRequest):
    """
      中断恢复接口

      当中断发生后，前端收集用户决策/补充数据，通过此端点恢复 Agent 执行。
      request.resume 的格式取决于中断类型：
      - 数据补充: {"supplement": "用户输入的补充信息"}
      - HITL 审批: {"decisions": [{"type": "approve"}]} 或 [{"type": "reject"}]
      """
    user_id = request.user_id
    resume = request.resume

    return StreamingResponse(
        stream_chat_response(thread_id=thread_id, resume_data=resume, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/chat/{thread_id}")
async def chat_get(thread_id: str):
    """
    获取对话内容
    """
    try:
        messages = await agent_loader.get_current_messages(thread_id)

        message_list = []
        for index, message in enumerate(messages):
            msg = Message(
                id=f"msg_{index}",
                role=message.get("role", "assistant"),
                content=message.get("content", ""),
                created_at=datetime.now(),
                tool_calls=message.get("tool_calls", []),
                tool_call_id=message.get("tool_call_id", ""),
            )
            message_list.append(msg)

        return ChatResponse(
            thread_id=thread_id,
            messages=message_list
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/{thread_id}/history")
async def chat_history(
        thread_id: str,
        limit: int = Query(50, ge=1, le=100, description="每页数量")):
    """获取会话历史状态列表"""
    try:
        status = await agent_loader.get_state_history(thread_id, limit)
        return {
            "thread_id": thread_id,
            "status": [
                {
                    "config": status.config,
                    "values": status.values,
                    "created_at": status.created_at,
                    "parent_config": status.parent_config
                }
                for status in status
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
