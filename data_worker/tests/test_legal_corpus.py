"""劳动法律资料条级切片与 prepared artifact 的纯本地测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from data_worker.ingest.legal_corpus import (
    DEFAULT_COLLECTION,
    LegalCorpusError,
    _is_protected_collection,
    ingest_prepared_corpus,
    prepare_legal_corpus,
    validate_prepared_corpus,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_source_document(base: Path) -> Path:
    raw_path = base / "raw" / "a_level" / "示例劳动法规_20260101.docx"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"official-word-fixture")

    markdown_path = base / "normalized" / "a_level" / "example_labor_rule.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "format_version": 1,
        "doc_id": "example_labor_rule_20260101",
        "title": "示例劳动法规",
        "source_level": "A",
        "target_collection": DEFAULT_COLLECTION,
        "document_type": "行政法规",
        "issuing_authority": "国务院",
        "jurisdiction": "PENDING_MANUAL_VERIFICATION",
        "national_applicability": "PENDING_MANUAL_VERIFICATION",
        "publication_date": "2026-01-01",
        "effective_date": "2026-01-01",
        "amendment_or_repeal_status": "有效",
        "official_url": "https://example.gov.cn/law/example",
        "raw_file": "raw/a_level/示例劳动法规_20260101.docx",
        "raw_sha256": _sha256(raw_path),
    }
    body = f"""---json
{json.dumps(header, ensure_ascii=False, indent=2)}
---

# 示例劳动法规

## 原文正文

（示例）经审议通过。

## 第一章 总则

#### 第一条 用人单位应当依法与劳动者订立书面劳动合同，并明确约定工作内容、劳动报酬和工作地点。

#### 第二条 用人单位应当依法参加社会保险，并按时足额缴纳社会保险费；劳动者依法享有社会保险待遇。
"""
    markdown_path.write_text(body, encoding="utf-8")
    metadata = dict(header)
    metadata.update(
        {
            "license_status": "PENDING_MANUAL_VERIFICATION",
            "normalized_markdown": "normalized/a_level/example_labor_rule.md",
            "normalized_markdown_sha256": _sha256(markdown_path),
        }
    )
    metadata_path = base / "metadata" / "a_level_documents.jsonl"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return markdown_path


class LegalCorpusTests(unittest.TestCase):
    def test_protected_collection_prefixes_are_rejected(self) -> None:
        self.assertTrue(_is_protected_collection("rag_chunks"))
        self.assertTrue(_is_protected_collection("rag_chunks_v2"))
        self.assertTrue(_is_protected_collection("watsonx_docsqa_colab_v2"))
        self.assertFalse(_is_protected_collection(DEFAULT_COLLECTION))

    def test_prepare_preserves_article_location_and_validates_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "labor_contract"
            _write_source_document(base)

            result = prepare_legal_corpus(
                base_dir=base,
                max_chars=30,
                overlap=8,
            )
            prepared = base / "prepared" / "a_level"
            validation = validate_prepared_corpus(prepared)
            articles = [
                json.loads(line)
                for line in (prepared / "articles.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            chunks = [
                json.loads(line)
                for line in (prepared / "article_chunks.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["articles"], 3)  # 前言 + 两条法律条文
        self.assertGreater(result["article_chunks"], result["articles"])
        self.assertEqual(validation["status"], "valid")
        self.assertEqual(validation["citation_eligible_articles"], 2)
        first_article = next(item for item in articles if item["article_no"] == "第一条")
        self.assertEqual(first_article["chapter"], "第一章 总则")
        self.assertTrue(first_article["citation_eligible"])
        self.assertTrue(any(item["article_id"] == first_article["article_id"] for item in chunks))
        self.assertTrue(all(item["point_id"] for item in chunks))

    def test_dry_run_requires_explicit_pending_governance_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "labor_contract"
            _write_source_document(base)
            prepare_legal_corpus(base_dir=base)
            prepared = base / "prepared" / "a_level"

            with self.assertRaises(LegalCorpusError):
                ingest_prepared_corpus(
                    prepared_dir=prepared,
                    qdrant_url="http://unused",
                    embed_url="http://unused",
                    dry_run=True,
                )
            result = ingest_prepared_corpus(
                prepared_dir=prepared,
                qdrant_url="http://unused",
                embed_url="http://unused",
                allow_pending_governance=True,
                dry_run=True,
            )

        self.assertEqual(result["status"], "dry_run_valid")
        self.assertGreater(result["points"], 0)

    def test_validation_rejects_tampered_prepared_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "labor_contract"
            _write_source_document(base)
            prepare_legal_corpus(base_dir=base)
            chunk_path = base / "prepared" / "a_level" / "article_chunks.jsonl"
            chunk_path.write_text(chunk_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            with self.assertRaises(LegalCorpusError):
                validate_prepared_corpus(base / "prepared" / "a_level")


if __name__ == "__main__":
    unittest.main()
