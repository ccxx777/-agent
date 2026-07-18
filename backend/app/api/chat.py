"""普通聊天 HTTP API。

本模块仅处理 ``POST /api/chat``。会话历史已拆到 ``api.sessions``，请求响应
模型位于 ``schemas.chat``，LangGraph 执行位于 ``ChatService``。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

logger = logging.getLogger("api.chat")


def create_chat_router(chat_service: ChatService) -> APIRouter:
    """创建只依赖 ``ChatService`` 的聊天路由。"""
    router = APIRouter(prefix="/api", tags=["Chat"])

    @router.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        """执行一次带会话上下文的知识库问答。"""
        query = request.query.strip()
        session_id = request.session_id.strip()
        user_id = request.user_id.strip() or "anonymous"

        if not query:
            raise HTTPException(status_code=400, detail="query 不能为空")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")

        try:
            answer = await chat_service.ask(
                query=query,
                session_id=session_id,
                user_id=user_id,
            )
        except Exception as error:
            logger.error("Agent execution failed: %s", error, exc_info=True)
            raise HTTPException(status_code=500, detail=f"内部执行错误: {error}") from error

        return ChatResponse(answer=answer, session_id=session_id)

    return router
