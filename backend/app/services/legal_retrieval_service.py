"""法律 Collection 的安全检索适配层。

通用 ``RetrievalService`` 负责调用现有 Cascade Funnel；本模块只负责把
Funnel 结果限制在指定法律等级，并在生成报告前执行可引用性与治理状态检查。
法律资料不应因为误配而落入通用 ``rag_chunks`` 或评测 Collection。
"""

from __future__ import annotations

from typing import Any

from app.schemas.retrieval import RetrievalPayload, RetrievedDocument


class LegalRetrievalError(RuntimeError):
    """法律 Collection 配置或资料治理状态不满足检索条件。"""


class LegalRetrievalService:
    """在已完成召回的结果上执行法律资料边界过滤。"""

    def __init__(
        self,
        *,
        retrieval_service: Any,
        collection_name: str,
        source_level: str = "A",
        allow_pending_governance: bool = False,
    ) -> None:
        normalized_collection = collection_name.strip()
        if not normalized_collection.startswith("legal_"):
            raise ValueError(
                "法律检索 Collection 必须使用 legal_ 前缀，禁止接入通用或评测 Collection"
            )
        if source_level not in {"A", "B"}:
            raise ValueError("source_level 只能是 A 或 B")
        self._retrieval_service = retrieval_service
        self._collection_name = normalized_collection
        self._source_level = source_level
        self._allow_pending_governance = allow_pending_governance

    @property
    def collection_name(self) -> str:
        """返回实际查询的独立法律 Collection，便于启动日志和 Smoke Test 使用。"""

        return self._collection_name

    @property
    def source_level(self) -> str:
        """返回本服务允许输出的法律资料等级。"""

        return self._source_level

    async def retrieve(self, query: str) -> RetrievalPayload:
        """查询并只返回满足法律引用门禁的文档。"""

        payload = await self._retrieval_service.retrieve(query)
        documents: list[RetrievedDocument] = []
        for document in payload.documents:
            metadata = getattr(document, "metadata", {}) or {}
            if metadata.get("source_level") != self._source_level:
                continue
            if metadata.get("citation_eligible") is not True:
                continue
            activation_status = str(metadata.get("legal_activation_status") or "")
            if activation_status != "ACTIVE" and not self._allow_pending_governance:
                continue
            documents.append(document)

        return self._build_payload(documents)

    @staticmethod
    def _build_payload(documents: list[RetrievedDocument]) -> RetrievalPayload:
        """重新编号过滤后的文档，保持生成上下文与最终引用顺序一致。"""

        ranked: list[RetrievedDocument] = []
        contexts: list[str] = []
        parts: list[str] = []
        for rank, document in enumerate(documents, 1):
            ranked_document = document.model_copy(update={"rank": rank})
            ranked.append(ranked_document)
            if ranked_document.context_text:
                contexts.append(ranked_document.context_text)
                source = ranked_document.source.replace("\\", "/")
                parts.append(
                    f"[{rank}] src:{source}\n{ranked_document.context_text}"
                )
        return RetrievalPayload(
            context="\n\n".join(parts) if parts else "(empty)",
            contexts=contexts,
            documents=ranked,
        )
