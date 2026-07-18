from __future__ import annotations

import unittest

from app.schemas.retrieval import build_retrieval_payload


class FakePoint:
    def __init__(self, point_id: str, text: str, source: str, score=None):
        self.id = point_id
        self.score = score
        self.payload = {
            "doc_id": f"doc-{point_id}",
            "chunk_id": f"doc-{point_id}/chunk_0000",
            "chunk_text": text,
            "title": f"title-{point_id}",
            "source": source,
        }


class RetrievalSchemaTests(unittest.TestCase):
    def test_preserves_rank_and_builds_generation_context(self):
        hits = [
            FakePoint("a", "甲" * 600, "/app/data/raw/a.md", 0.9),
            FakePoint("b", "乙" * 20, "/app/data/raw/b.md"),
        ]

        payload = build_retrieval_payload(hits)

        self.assertEqual([doc.rank for doc in payload.documents], [1, 2])
        self.assertEqual(len(payload.contexts[0]), 500)
        self.assertEqual(payload.documents[0].qdrant_score, 0.9)
        self.assertIsNone(payload.documents[1].qdrant_score)
        self.assertIn("[1] src:data/raw/a.md", payload.context)
        self.assertEqual(payload.contexts[0], payload.documents[0].context_text)

    def test_empty_hits_are_explicit(self):
        payload = build_retrieval_payload([])

        self.assertEqual(payload.context, "(empty)")
        self.assertEqual(payload.contexts, [])
        self.assertEqual(payload.documents, [])


if __name__ == "__main__":
    unittest.main()

