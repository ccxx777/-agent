"""会话历史 HTTP API。

为了兼容现有前端，路径仍为 ``GET /api/chat/history/{session_id}``。本模块只
负责错误边界和响应序列化，LangGraph 状态读取由 ``SessionService`` 完成。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.schemas.chat import SessionHistoryResponse
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)


def create_sessions_router(session_service: SessionService) -> APIRouter:
    """创建会话查询路由，保持历史 URL 不变。"""
    router = APIRouter(prefix="/api", tags=["Sessions"])

    @router.get("/chat/history/{session_id}", response_model=SessionHistoryResponse)
    async def chat_history(session_id: str) -> SessionHistoryResponse:
        """返回指定会话的用户/AI 消息和压缩摘要。"""
        try:
            result = await session_service.get_history(session_id)
        except Exception as error:
            logger.error("aget_state failed for %s: %s", session_id, error)
            result = {"messages": [], "summary": ""}
        return SessionHistoryResponse.model_validate(result)

    return router
