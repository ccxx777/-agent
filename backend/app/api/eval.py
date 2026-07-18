"""RAG 评测专用 HTTP API。

端点复用与普通聊天完全相同的 ``ChatService`` 和 LangGraph，只额外从
ToolMessage 提取生成时真实使用的 contexts 与结构化 documents。评测代码
因此不会绕过 Agent 或另建一套召回路径。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from app.schemas.retrieval import RetrievedDocument
from app.services.chat_service import ChatService

logger = logging.getLogger("api.eval")


class EvalRagQueryRequest(BaseModel):
    """单条 RAG 评测请求。"""

    question: str = Field(..., min_length=1)


class EvalRagQueryResponse(BaseModel):
    """答案、实际生成上下文和完整召回元数据。"""

    answer: str
    contexts: list[str]
    documents: list[RetrievedDocument] = Field(default_factory=list)


def _parse_contexts(tool_content: str) -> list[str]:
    """兼容旧 ToolMessage 文本格式，拆出上下文列表。"""
    if not tool_content or tool_content == "(empty)":
        return []
    chunks = []
    for part in tool_content.split("\n\n"):
        part = part.strip()
        if not part:
            continue
        lines = part.split("\n", 1)
        if len(lines) == 2:
            chunks.append(lines[1].strip())
        else:
            chunks.append(part)
    return chunks


def _extract_retrieval_result(tool_content: str) -> tuple[list[str], list[RetrievedDocument]]:
    """读取新版结构化 ToolMessage，并兼容历史 context 字符串。"""
    try:
        tool_result = json.loads(tool_content or "{}")
    except json.JSONDecodeError:
        return [], []

    contexts = tool_result.get("contexts")
    if not isinstance(contexts, list):
        contexts = _parse_contexts(str(tool_result.get("context") or ""))
    else:
        contexts = [str(item) for item in contexts if str(item).strip()]

    documents = []
    for item in tool_result.get("documents") or []:
        try:
            documents.append(RetrievedDocument.model_validate(item))
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid retrieval document in eval payload")

    return contexts, documents


def create_eval_router(chat_service: ChatService) -> APIRouter:
    """创建复用生产 ChatService 的评测路由。"""
    import uuid

    router = APIRouter(prefix="/api/eval", tags=["Eval"])

    @router.post("/rag_query", response_model=EvalRagQueryResponse)
    async def rag_query(req: EvalRagQueryRequest) -> EvalRagQueryResponse:
        thread_id = f"eval_http_{uuid.uuid4().hex[:8]}"

        result: dict[str, Any] = await chat_service.invoke(
            query=req.question,
            session_id=thread_id,
            user_id="evaluation",
        )

        messages = result.get("messages", [])

        answer = ""
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                answer = str(m.content or "").strip()
                break

        contexts: list[str] = []
        documents: list[RetrievedDocument] = []
        for m in messages:
            if isinstance(m, ToolMessage):
                contexts, documents = _extract_retrieval_result(str(m.content or "{}"))
                break

        return EvalRagQueryResponse(answer=answer, contexts=contexts, documents=documents)

    return router
