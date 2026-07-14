from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

# =============================================================================
# ★ 1. 运行时上下文 —— ProcurementContext 和 UserPreferences 数据类
# =============================================================================
@dataclass
class ProcurementContext:
    """
    运行时上下文，由调用方在 invoke 时传入。
    用于传递当前用户身份等基础信息。
    """
    user_id: str                # 必填，用户唯一标识
    username: str               # 必填，用户姓名/登录名


@dataclass
class UserPreferences:
    """
    用户偏好数据结构，存储在长期记忆文件中。
    对应 /memories/{user_id}/preferences.md 的内容
    """

    preferred_output: Optional[str] = None # 'table' 或 'chart'
    preferred_chart_type: Optional[str] = None  # 'bar', 'line', 'pie' 等
    preferred_currency: Optional[str] = None  # 'CNY', 'USD' 等
    preferred_language: Optional[str] = None  # 'zh', 'en' 等
    recent_suppliers: list[str] = None  # 近期使用的供应商列表
    recent_queries: list[str] = None  # 近期分析需求摘要列表

    def __post_init__(self):
        """初始化后处理：将 None 值的可变字段默认化为空列表，避免共享可变默认值。"""
        if self.recent_suppliers is None:
            self.recent_suppliers = []
        if self.recent_queries is None:
            self.recent_queries = []

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
