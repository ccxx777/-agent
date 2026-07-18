"""Chat 与 Session HTTP 数据契约。

Schema 只描述外部可见的数据形状，不包含 Graph、数据库或路由逻辑。将模型
集中在这里可以避免 API 文件在重构过程中悄悄改变请求和响应格式。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """普通对话请求。``session_id`` 同时作为 LangGraph thread_id。"""

    query: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    user_id: str = Field(default="anonymous")


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
