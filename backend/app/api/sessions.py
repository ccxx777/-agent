"""会话历史 HTTP API。

为了兼容现有前端，路径仍为 ``GET /api/chat/history/{session_id}``。本模块只
负责错误边界和响应序列化，LangGraph 状态读取由 ``SessionService`` 完成。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.infrastructure.contract_review_repository import SessionOwnershipError
from app.schemas.chat import SessionHistoryResponse, SessionReviewsResponse
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)


def create_sessions_router(
    session_service: SessionService,
    review_repository: object | None = None,
) -> APIRouter:
    """创建会话查询路由，保持历史 URL 不变。"""
    router = APIRouter(prefix="/api", tags=["Sessions"])

    @router.get("/chat/history/{session_id}", response_model=SessionHistoryResponse)
    async def chat_history(
        session_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> SessionHistoryResponse:
        """返回指定会话的用户/AI 消息和压缩摘要。"""
        try:
            result = await session_service.get_history(session_id, user["user_id"])
        except SessionOwnershipError as error:
            raise HTTPException(status_code=403, detail="无权访问该会话") from error
        except Exception as error:
            logger.error("aget_state failed for %s: %s", session_id, error)
            result = {"messages": [], "summary": ""}
        return SessionHistoryResponse.model_validate(result)

    @router.get("/sessions/{session_id}/reviews", response_model=SessionReviewsResponse)
    async def session_reviews(
        session_id: str,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> SessionReviewsResponse:
        """返回当前用户会话绑定的合同任务和最新报告标识。"""

        if review_repository is None or not hasattr(review_repository, "list_session_reviews"):
            return SessionReviewsResponse(session_id=session_id, reviews=[])
        result = await review_repository.list_session_reviews(session_id, user["user_id"])
        return SessionReviewsResponse(session_id=session_id, reviews=result)

    return router
