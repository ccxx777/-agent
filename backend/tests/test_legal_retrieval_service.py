from __future__ import annotations

import unittest
from typing import ClassVar

from app.schemas.retrieval import (
    RetrievalPayload,
    RetrievedDocument,
    build_retrieval_payload,
)
from app.services.legal_retrieval_service import LegalRetrievalService


def _document(
    *,
    doc_id: str,
    source_level: str = "A",
    citation_eligible: bool = True,
    activation_status: str = "PENDING_LEGAL_REVIEW",
) -> RetrievedDocument:
    return RetrievedDocument(
        doc_id=doc_id,
        chunk_id=f"{doc_id}/article_001/chunk_000",
        title="劳动合同法",
        source="https://flk.npc.gov.cn/search",
        text=f"{doc_id} 官方法条正文",
        context_text=f"{doc_id} 官方法条正文",
        rank=1,
        metadata={
            "source_level": source_level,
            "citation_eligible": citation_eligible,
            "citation_label": f"《劳动合同法》{doc_id}",
            "article_no": doc_id,
            "official_url": "https://flk.npc.gov.cn/search",
            "effective_date": "2013-07-01",
            "legal_activation_status": activation_status,
        },
    )


class _Retrieval:
    def __init__(self, documents: list[RetrievedDocument]) -> None:
        self.documents = documents

    async def retrieve(self, query: str) -> RetrievalPayload:
        return RetrievalPayload(
            context="raw",
            contexts=[document.context_text for document in self.documents],
            documents=self.documents,
        )


class LegalRetrievalServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_retrieval_adapter_preserves_legal_metadata(self):
        class Point:
            id = "point-1"
            score = 0.9
            payload: ClassVar[dict[str, object]] = {
                "doc_id": "law-1",
                "chunk_id": "law-1/article_001/chunk_000",
                "title": "劳动合同法",
                "source": "https://flk.npc.gov.cn/search",
                "chunk_text": "第一条 官方法条正文",
                "source_level": "A",
                "citation_eligible": True,
                "article_no": "第一条",
                "citation_label": "《劳动合同法》第一条",
                "official_url": "https://flk.npc.gov.cn/search",
                "legal_activation_status": "PENDING_LEGAL_REVIEW",
            }

        payload = build_retrieval_payload([Point()])

        self.assertEqual(payload.documents[0].metadata["article_no"], "第一条")
        self.assertTrue(payload.documents[0].metadata["citation_eligible"])

    async def test_pending_corpus_is_fail_closed_without_explicit_staging_flag(self):
        service = LegalRetrievalService(
            retrieval_service=_Retrieval([_document(doc_id="第一条")]),
            collection_name="legal_labor_a_v1",
        )

        result = await service.retrieve("劳动合同")

        self.assertEqual(result.documents, [])
        self.assertEqual(result.context, "(empty)")

    async def test_staging_flag_keeps_only_citation_eligible_a_level_documents(self):
        service = LegalRetrievalService(
            retrieval_service=_Retrieval(
                [
                    _document(doc_id="第一条"),
                    _document(doc_id="前言", citation_eligible=False),
                    _document(doc_id="B-案例", source_level="B"),
                ]
            ),
            collection_name="legal_labor_a_v1",
            allow_pending_governance=True,
        )

        result = await service.retrieve("劳动合同")

        self.assertEqual([document.doc_id for document in result.documents], ["第一条"])
        self.assertEqual(result.documents[0].rank, 1)
        self.assertIn("[1] src:https://flk.npc.gov.cn/search", result.context)
        self.assertEqual(result.documents[0].metadata["article_no"], "第一条")

    def test_rejects_non_legal_collection(self):
        with self.assertRaises(ValueError):
            LegalRetrievalService(
                retrieval_service=_Retrieval([]),
                collection_name="rag_chunks",
            )


if __name__ == "__main__":
    unittest.main()
