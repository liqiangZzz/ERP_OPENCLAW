from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# =============================================================================
# ★ 2. 对话相关模型 —— ChatRequest、Message、ChatResponse
# =============================================================================

class ChatRequest(BaseModel):
    """对话请求模型"""
    message: str = Field(..., description="用户消息")
    thread_id: Optional[str] = Field(None, description="会话 ID，为空则创建新会话")
    user_id: str = Field(..., description="用户唯一标识")


class Message(BaseModel):
    """消息模型"""

    id: str = Field(..., description="消息唯一标识")
    role: str = Field(..., description="消息角色： user/assistant/tool")
    content: str = Field("", description="消息内容")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="工具调用信息")
    tool_call_id: Optional[str] = Field(None, description="工具调用 ID")
    source: Optional[str] = Field(None, description="消息来源： main 或子代理名称")

    # 工具消息专属字段（仅在 role='tool' 时有意义）
    tool_name: Optional[str] = Field(None, description="工具名称")
    tool_status: Optional[str] = Field(None, description="工具状态： calling/ done")
    text: Optional[str] = Field(None, description="工具结果文本")
    images: Optional[List[str]] = Field(None, description="工具结果图片列表")
    args: Optional[str] = Field(None, description="工具调用参数")


class ChatResponse(BaseModel):
    """对话相应模型"""
    thread_id: str = Field(..., description="会话 Id")
    messages: List[Message] = Field(default_factory=list, description="消息列表")


# =============================================================================
# ★ 3. 历史记录相关模型 —— Session、SessionListResponse 等
# =============================================================================

class Session(BaseModel):
    """会话模型"""
    thread_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最后更新时间")
    message_count: int = Field(0, description="消息数量")


class SessionListResponse(BaseModel):
    """会话列表响应模型"""
    sessions: List[Session] = Field(default_factory=list, description="会话列表")
    total: int = Field(0, description="会话总数")
    page: int = Field(1, description="当前页码")
    limit: int = Field(10, description="每页数量")


class SessionMessagesResponse(BaseModel):
    """会话消息历史响应模型"""
    thread_id: str = Field(..., description="会话 ID")
    messages: List[Message] = Field(default_factory=list, description="消息列表")


class DeleteSessionResponse(BaseModel):
    """删除会话响应模式"""
    success: bool = Field(True, description="是否成功")
    message: str = Field("会话已删除", description="响应消息")
