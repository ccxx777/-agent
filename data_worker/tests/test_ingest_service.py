"""入库编排测试，不连接 PostgreSQL、Qdrant 或 Embedding Service。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_worker.ingest.service import IngestService


class _Fingerprints:
    def __init__(self, existing=False):
        self.existing = existing
        self.recorded = None

    def find(self, sha256):
        return (self.existing, self.source if self.existing else None)

    def record_document(self, **kwargs):
        self.recorded = kwargs


class _Chunker:
    def split(self, text):
        return [text]


class _Embedder:
    def embed(self, chunks):
        return ([[0.1, 0.2]], [{"7": 0.8}])


class _Writer:
    def write(self, **kwargs):
        return ("doc-id", "title", len(kwargs["chunks"]))


class IngestServiceTests(unittest.TestCase):
    def test_stored_document_records_fingerprint_after_write(self):
        fingerprints = _Fingerprints()
        service = IngestService(
            fingerprints=fingerprints,
            chunker=_Chunker(),
            embedder=_Embedder(),
            writer=_Writer(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "document.md"
            file_path.write_text("content", encoding="utf-8")
            result = service.ingest(file_path)

        self.assertEqual(result["status"], "stored")
        self.assertEqual(result["chunks"], 1)
        self.assertEqual(fingerprints.recorded["doc_id"], "doc-id")


if __name__ == "__main__":
    unittest.main()
