"""Chat 与 Session HTTP 数据契约。

Schema 只描述外部可见的数据形状，不包含 Graph、数据库或路由逻辑。将模型
集中在这里可以避免 API 文件在重构过程中悄悄改变请求和响应格式。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """统一会话请求。

    ``session_id`` 是用户看到的连续对话；``review_id`` 只在法律/报告模式下
    指向一份具体合同任务，避免多份合同在同一会话中串上下文。
    """

    query: str = Field(..., min_length=1)
    session_id: UUID
    user_id: str = Field(default="anonymous")
    mode: Literal["general", "legal", "contract_review"] = "general"
    review_id: UUID | None = None


class ChatResponse(BaseModel):
    """普通对话响应，保持原 API 字段不变。"""

    answer: str
    session_id: str


class SessionMessage(BaseModel):
    """对外展示的一条用户或 AI 历史消息。"""

    role: str
    content: str


class SessionHistoryResponse(BaseModel):
    """会话消息与压缩摘要。"""

    messages: list[SessionMessage] = Field(default_factory=list)
    summary: str = ""


class SessionReviewSummary(BaseModel):
    """同一用户会话中可切换的合同审查任务。"""

    review_id: str
    session_id: str | None = None
    filename: str
    status: str
    confirmation_status: str
    report_id: str | None = None
    report_version: int | None = None
    created_at: datetime | None = None


class SessionReviewsResponse(BaseModel):
    """会话绑定的合同任务列表。"""

    session_id: str
    reviews: list[SessionReviewSummary] = Field(default_factory=list)
