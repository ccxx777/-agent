"""普通聊天 HTTP API。

本模块仅处理 ``POST /api/chat``。会话历史已拆到 ``api.sessions``，请求响应
模型位于 ``schemas.chat``，LangGraph 执行位于 ``ChatService``。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse

from app.api.deps import get_current_user
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import (
    ChatReportNotFound,
    ChatReportSessionMismatch,
    ChatService,
)

logger = logging.getLogger("api.chat")


def create_chat_router(chat_service: ChatService) -> APIRouter:
    """创建只依赖 ``ChatService`` 的聊天路由。"""
    router = APIRouter(prefix="/api", tags=["Chat"])

    @router.post("/chat", response_model=ChatResponse)
    async def chat(
        request: ChatRequest,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> ChatResponse:
        """执行一次带会话上下文的知识库问答。"""
        query = request.query.strip()
        session_id = str(request.session_id)
        user_id = user["user_id"]

        if not query:
            raise HTTPException(status_code=400, detail="query 不能为空")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")

        try:
            answer = await chat_service.ask(
                query=query,
                session_id=session_id,
                user_id=user_id,
                mode=request.mode,
                review_id=request.review_id,
            )
        except ChatReportNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ChatReportSessionMismatch as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            logger.exception("Agent execution failed")
            raise HTTPException(status_code=500, detail=f"内部执行错误: {error}") from error

        return ChatResponse(answer=answer, session_id=session_id)

    @router.post("/chat/stream")
    async def chat_stream(
        request: ChatRequest,
        user: dict = Depends(get_current_user),  # noqa: B008 - FastAPI dependency declaration
    ) -> StreamingResponse:
        """兼容前端 SSE；当前模型非流式，因此以单帧答案发送。"""

        query = request.query.strip()
        session_id = str(request.session_id)
        if not query:
            raise HTTPException(status_code=400, detail="query 不能为空")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id 不能为空")
        try:
            answer = await chat_service.ask(
                query=query,
                session_id=session_id,
                user_id=user["user_id"],
                mode=request.mode,
                review_id=request.review_id,
            )
        except ChatReportNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ChatReportSessionMismatch as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            logger.exception("Agent stream execution failed")
            raise HTTPException(status_code=500, detail="对话服务暂时不可用") from error

        async def events():
            yield f"data: {json.dumps({'type': 'token', 'content': answer}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return router
