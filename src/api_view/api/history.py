"""
历史记录 API 模块

提供会话列表查询、会话消息历史获取和会话删除接口
"""
import re
from datetime import datetime
from typing import Any, List, Optional, Dict

from fastapi import APIRouter, Query, HTTPException

from agent.scheam import SessionListResponse, Session, SessionMessagesResponse, Message, DeleteSessionResponse
from api_view.agent_loader import agent_loader

# 创建路由
router = APIRouter()


# ============================================================
# 辅助函数：从 LangChain 消息对象中提取数据
# ============================================================

# 从消息对象获取属性
# 兼容 dict 和对象两种格式
def get_message_attr(msg: Any, attr: str, default: Any = None) -> Any:
    """从消息对象中获取属性值（兼容 dict 和对象格式） """
    if isinstance(msg, dict):
        return msg.get(attr, default)
    return getattr(msg, attr, default)


# 获取消息角色
# LangChain → 前端映射：`human→user`、`ai→assistant`、`ToolMessage→tool`
def get_message_role(msg: Any):
    """获取消息角色： user/assistant/tool"""

    role = get_message_attr(msg, "role")

    if role:
        if role == 'human': return 'user'
        if role == 'ai': return 'assistant'
        return role

    msg_type = type(msg).__name__

    if msg_type == 'HumanMessage':
        return 'user'
    elif msg_type == 'AIMessage':
        return 'assistant'
    elif msg_type == 'ToolMessage':
        return 'tool'
    return "assistant"


# 提取纯文本内容
# 兼容 str/list/dict 三种 content 格式
def get_message_content(msg: Any) -> str:
    """
    从消息对象中提取纯文本内容
    处理 content 可能是 str / list / dict 情况
    """

    if isinstance(msg.content, dict):
        content = msg.content.get('content', "")
    else:
        content = getattr(msg, "content", "")

    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif "text" in item:
                    text_parts.append(str(item["text"]))
                elif "content" in item:
                    text_parts.append(str(item["content"]))
            else:
                text_parts.append(str(item))
        return ''.join(text_parts)
    elif isinstance(content, dict):
        return content.get("text", str(content))
    else:
        return str(content) if content else ""


def extract_images_from_content(content) -> List[str]:
    """
    从消息 content 中提取图片 URL 列表
    支持结构化列表和 markdown 图片语法
    """
    images = []

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "image_url":
                    image_url = item.get("image_url", {})
                    url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url)
                    if url:
                        images.append(url)
                elif item.get("type") == "image":
                    data = item.get("data") or item.get("image_data", "")
                    if data:
                        images.append(data)
    elif isinstance(content, str):
        # 提取 markdown 图片语法
        for match in re.finditer(r'!\[.*?\]\((.*?)\)', content):
            url = match.group(1)
            if url not in images:
                images.append(url)
        # 提取 data:image base64
        for match in re.finditer(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', content):
            url = match.group(0)
            if url not in images:
                images.append(url)

    return images


# 工具调用格式化
# 从 AIMessage 中提取 `tool_calls` 列表，转换为统一格式：
# [
#   {
#     "name": "工具名",
#     "args": "参数（字符串）",
#     "result": "",
#     "source": "main"
#   }
# ]
def format_tool_calls(msg: Any) -> Optional[List[Dict[str, Any]]]:
    """从 AIMessage 中提取工具调用信息"""
    tool_calls = get_message_attr(msg, "tool_calls")

    if not tool_calls or not isinstance(tool_calls, list):
        return None

    result = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            result.append({
                "name": tc.get("name", ""),
                "args": tc.get("args", ""),
                "result": "",
                "source": "main"
            })
        else:
            name = getattr(tc, "name", "unknown")
            args = getattr(tc, "args", "")
            if isinstance(args, dict):
                args = str(args)
            result.append({
                "name": name,
                "args": args,
                "result": "",
                "source": "main"
            })

    return result if result else None


# 将 MongoDB checkpoint 中的 LangChain 原始消息转换为前端需要的 Message 格式
#### 为什么要拆分 AIMessage？

# LangGraph 的 checkpoint 中，一条 AIMessage 可能同时包含文本内容和工具调用：
#
# ```
# 原始 AIMessage:
#   content: "我来帮你搜索一下"
#   tool_calls: [{name: "web_search", args: {"query": "..."}}]
#
# 转换后：
#   1. {role: "assistant", content: "我来帮你搜索一下"}
#   2. {role: "tool", tool_name: "web_search", args: ..., tool_status: "calling"}
#      └── 后续 ToolMessage 到达时填充结果，变为 tool_status: "done"
# ```
#
# 这样前端展示与流式过程一致：AI 说"我来搜索" → 工具调用中 → 工具返回结果。
def serialize_messages_from_checkpoint(messages: list) -> list:
    """
       将 MongoDB checkpoint 中存储的 LangChain 消息列表
       转换为前端需要的 Message 格式

       改进点：
       1. AIMessage 若有 tool_calls，拆分为「AI 文本」+「Tool 占位」两条消息，
          使前端展示与流式过程一致（文本和工具调用交替出现）
       2. ToolMessage 优先匹配前一条 AIMessage 中同名的 tool_calls 占位，
          填充 text/images 字段，避免重复创建 tool 消息

       Args:
           messages: LangChain 消息对象列表

       Returns:
           list: Message schema 兼容的字典列表
       """
    result = []
    for index, msg in enumerate(messages):
        role = get_message_role(msg)
        content = get_message_content(msg)

        if role == "assistant":
            # 有 content → 添加 AI 文本消息
            if content:
                result.append({
                    "id": f"msg-{index}",
                    "role": "assistant",
                    "content": content,
                    "source": "main",
                    "created_at": datetime.now(),
                })

            # 有 tool_calls → 为每个 tool_call 创建占位消息
            tools_calls = get_message_attr(msg, "tool_calls")
            if tools_calls and isinstance(tools_calls, list):
                for tool_call in tools_calls:
                    tc_name = tool_call.get("name", "未知工具") \
                        if isinstance(tool_call, dict) else getattr(tool_call, "name", "未知工具")
                    if isinstance(tool_call, dict):
                        tc_args = tool_call.get("args", "")
                    else:
                        args_val = getattr(tool_call, "args", "")
                        tc_args = str(args_val) if args_val else ""

                    result.append({
                        "id": f"msg-{index}-tc-{tc_name}",
                        "role": "tool",
                        "tool_name": tc_name,
                        "args": tc_args,
                        "text": "",
                        "images": [],
                        "source": "main",
                        "created_at": datetime.now(),
                    })


        elif role == "tool":
            # ToolMessage :尝试匹配前面的 tool 占位并填充结果
            tool_name = get_message_attr(msg, "name") or "未知工具"
            raw_content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            images = extract_images_from_content(raw_content)

            # 从后向前查找同名的空 tool 消息（calling 状态），填充结果
            found = False
            for prev in reversed(result):
                if (prev["role"] == "tool"
                        and prev["tool_name"] == tool_name
                        and prev["tool_status"] == "calling"
                        and not prev["text"]):
                    prev["text"] = content
                    prev["images"] = images
                    prev["tool_status"] = "done"
                    found = True
                    break

            # 未找到 → 新建 tool 消息（tool_status="done"）
            if not found:
                result.append({
                    "id": f"msg-{index}",
                    "role": "tool",
                    "tool_name": tool_name,
                    "args": "",
                    "text": content,
                    "images": images,
                    "source": "main",
                    "tool_status": "done",
                    "created_at": datetime.now(),
                })

        elif role == "user":
            # 直接添加用户消息
            result.append({
                "id": f"msg-{index}",
                "role": "user",
                "content": content,
                "created_at": datetime.now(),
            })
    return result


# ============================================================
# API 路由
# ============================================================

@router.get("/history", response_model=SessionListResponse)
async def get_sessions(
        page: int = Query(1, ge=1, description="当前页码"),
        limit: int = Query(20, ge=1, le=100, description="每页数量")
):
    """获取会话列表"""
    try:

        #  获取所有会话列表
        all_thread_ids = agent_loader.get_all_thread_ids()
        sessions = []
        for thread_id in all_thread_ids:
            try:
                # 获取当前会话消息
                messages = await agent_loader.get_current_messages(thread_id)

                if messages:
                    first_user_msg = next(
                        (m for m in messages if get_message_role(m) == "user"), None
                    )
                    if first_user_msg:
                        first_content = get_message_content(first_user_msg)
                        title = first_content[:50] if first_content else "新对话"
                        if len(first_content) > 50:
                            title += "..."
                    else:
                        title = "新对话"

                    message_count = len(messages) // 2

                    # 获取会话更新时间
                    updated_at = agent_loader.get_session_updated_at(thread_id)
                    sessions.append(Session(
                        thread_id=thread_id,
                        title=title,
                        message_count=message_count,
                        created_at=updated_at,
                        updated_at=updated_at
                    ))
            except Exception as e:
                print(f"Error processing session {thread_id}: {e}")
                continue

        # 按更新时间倒序排序
        sessions.sort(key=lambda x: x.updated_at, reverse=True)

        total = len(sessions)
        start = (page - 1) * limit
        end = start + limit
        paginated_sessions = sessions[start:end]

        return SessionListResponse(
            sessions=paginated_sessions,
            total=total,
            page=page,
            limit=limit
        )
    except Exception as e:
        print(f"[HistoryAPI] 获取会话列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{thread_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(thread_id: str):
    """
    获取会话消息里历史

    优先从 MongoDB display_messages 集合读取（包含子代理消息）
    如果不存在则回退到 checkpointer 序列化（兼容旧会话数据）。
    """
    try:
        # 优先从 MongoDB 读取流式过程中保存的完整展示消息
        serialized = await agent_loader.get_display_messages(thread_id)

        if not serialized:
            # 回退到 checkpointer （兼容旧数据，不含子代理消息）
            # 从 MongoDB 读取流式过程中保存的完整展示消息
            messages = await agent_loader.get_current_messages(thread_id)
            serialized = serialize_messages_from_checkpoint(messages)

        message_list = []
        for item in serialized:
            message = Message(
                id=item.get("id", ""),
                role=item.get("role", ""),
                content=item.get("content", ""),
                created_at=item.get("created_at", datetime.now()),
                tool_calls=item.get("tool_calls"),
                tool_call_id=item.get("tool_call_id"),
                source=item.get("source"),
                tool_name=item.get("tool_name"),
                tool_status=item.get("tool_status"),
                text=item.get("text"),
                args=item.get("args"),
                images=item.get("images")
            )
            message_list.append(message)
        return SessionMessagesResponse(
            thread_id=thread_id,
            messages=message_list
        )
    except Exception as e:
        print(f"[HistoryAPI] 获取会话消息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history/{thread_id}", response_model=DeleteSessionResponse)
async def delete_session(thread_id: str):
    """删除会话"""
    try:
        success = await agent_loader.delete_session(thread_id)

        if success:
            return DeleteSessionResponse(success=True, message="会话已删除")
        else:
            return DeleteSessionResponse(success=False, message="删除会话失败")
    except Exception as e:
        print(f"[HistoryAPI] 删除会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/history/{thread_id}")
async def update_session_title(thread_id: str, title: str = Query(..., description="新的会话标题")):
    """更新会话标题"""
    return {
        "success": True,
        "message": "标题更新功能待实现",
        "thread_id": thread_id,
        "title": title,
    }
