"""召回结果适配层。

该模块只读取 Cascade Funnel 已经排好序的 Qdrant Point，不参与召回、
融合或重排计算。列表顺序是最终排名的唯一事实来源。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

LLM_CONTEXT_CHARS = 500


class RetrievedDocument(BaseModel):
    """Funnel 最终结果中的单个文档块。

    ``text`` 保留完整原文，``context_text`` 是实际提供给生成模型的截断文本，
    ``rank`` 来自 Funnel 返回顺序，``qdrant_score`` 只表示原始数据库分数。
    """

    point_id: str = ""
    doc_id: str = ""
    chunk_id: str = ""
    title: str = ""
    source: str = ""
    text: str = ""
    context_text: str = ""
    rank: int = Field(..., ge=1)
    qdrant_score: float | None = None
    # 保留法律资料的审计元数据；通用 RAG 记录通常为空，不改变原有召回契约。
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalPayload(BaseModel):
    """Agent ToolMessage、生成节点和 Eval API 共用的检索契约。

    ``context`` 是带引用编号的 Prompt 文本；``contexts`` 是无装饰的实际上下文
    列表；``documents`` 用于召回指标和问题定位。
    """

    context: str
    contexts: list[str]
    documents: list[RetrievedDocument]


def _payload_of(hit: Any) -> dict[str, Any]:
    payload = getattr(hit, "payload", None)
    if isinstance(payload, dict):
        return payload
    if isinstance(hit, dict) and isinstance(hit.get("payload"), dict):
        return hit["payload"]
    return {}


def _point_id_of(hit: Any) -> str:
    point_id = getattr(hit, "id", None)
    if point_id is None and isinstance(hit, dict):
        point_id = hit.get("id", "")
    return str(point_id or "")


def _qdrant_score_of(hit: Any) -> float | None:
    """返回 Qdrant 原始分数；它不是 Funnel 最终分数。"""
    score = getattr(hit, "score", None)
    if score is None and isinstance(hit, dict):
        score = hit.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    return None


def adapt_ranked_hits(
    hits: list[Any],
    *,
    context_chars: int = LLM_CONTEXT_CHARS,
) -> list[RetrievedDocument]:
    """把已完成重排的 Point 列表转换为稳定 DTO，不改变顺序。"""
    documents: list[RetrievedDocument] = []
    metadata_fields = (
        "source_level",
        "citation_eligible",
        "citation_label",
        "article_no",
        "article_label",
        "chapter",
        "section",
        "effective_date",
        "official_url",
        "legal_activation_status",
        "document_type",
        "issuing_authority",
        "jurisdiction",
        "national_applicability",
        "publication_date",
        "amendment_or_repeal_status",
    )
    for rank, hit in enumerate(hits, 1):
        payload = _payload_of(hit)
        text = str(payload.get("chunk_text") or "")
        metadata = {
            key: payload[key]
            for key in metadata_fields
            if key in payload
        }
        documents.append(
            RetrievedDocument(
                point_id=_point_id_of(hit),
                doc_id=str(payload.get("doc_id") or ""),
                chunk_id=str(payload.get("chunk_id") or ""),
                title=str(payload.get("title") or ""),
                source=str(payload.get("source") or ""),
                text=text,
                context_text=text[:context_chars],
                rank=rank,
                qdrant_score=_qdrant_score_of(hit),
                metadata=metadata,
            )
        )
    return documents


def _display_source(source: str) -> str:
    normalized = source.replace("\\", "/")
    if "/data/" in normalized:
        return "data/" + normalized.split("/data/", 1)[1]
    return normalized


def build_retrieval_payload(hits: list[Any]) -> RetrievalPayload:
    """构建 Agent 生成与评测共用的同一份上下文。"""
    documents = adapt_ranked_hits(hits)
    contexts = [doc.context_text for doc in documents if doc.context_text]

    parts = [
        f"[{doc.rank}] src:{_display_source(doc.source)}\n{doc.context_text}"
        for doc in documents
        if doc.context_text
    ]
    context = "\n\n".join(parts) if parts else "(empty)"
    return RetrievalPayload(context=context, contexts=contexts, documents=documents)
