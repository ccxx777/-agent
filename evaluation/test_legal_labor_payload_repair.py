"""法律 Qdrant payload 修复工具的纯单元测试。"""

from __future__ import annotations

from evaluation.legal_labor_payload_repair import _payload


def test_payload_rebuild_contains_citation_and_fulltext_fields() -> None:
    record = {
        "point_id": "p1",
        "doc_id": "law",
        "chunk_id": "law/chunk_0001",
        "chunk_text": "第一条 用人单位应当依法订立劳动合同。",
        "title": "劳动合同法",
        "official_url": "https://flk.npc.gov.cn/detail?id=law",
        "raw_file": "raw/a_level/law.docx",
        "raw_sha256": "raw-hash",
        "source_level": "A",
        "document_type": "法律",
        "issuing_authority": "全国人民代表大会常务委员会",
        "jurisdiction": "中国大陆",
        "national_applicability": True,
        "publication_date": "2012-12-28",
        "effective_date": "2013-07-01",
        "amendment_or_repeal_status": "有效",
        "official_source_id": "law",
        "article_id": "law/article_001",
        "article_no": "第一条",
        "article_label": "第一条",
        "article_ordinal": 1,
        "chapter": "第一章",
        "section": None,
        "citation_label": "《劳动合同法》 第一条",
        "citation_eligible": True,
        "article_text": "第一条 用人单位应当依法订立劳动合同。",
        "article_text_sha256": "article-hash",
        "article_start": 0,
        "article_end": 21,
        "excerpt_text": "第一条 用人单位应当依法订立劳动合同。",
        "excerpt_sha256": "excerpt-hash",
        "source_file_origin": "MAINTAINER_ATTESTED_OFFICIAL_DOWNLOAD",
        "legal_activation_status": "ACTIVE",
        "license_status": "OFFICIAL_PUBLIC_SOURCE",
        "content_match_status": "VERIFIED_OFFICIAL_TEXT_MATCH",
        "review_status": "LEGAL_REVIEW_CONFIRMED",
        "reviewed_by": "ccxx",
        "reviewed_at": "2026-08-04T00:00:00+00:00",
    }

    payload = _payload(record)

    assert payload["source_level"] == "A"
    assert payload["citation_eligible"] is True
    assert payload["article_no"] == "第一条"
    assert payload["official_url"].startswith("https://flk.npc.gov.cn/")
    assert payload["legal_activation_status"] == "ACTIVE"
    assert payload["fulltext_en"] == record["chunk_text"]
    assert payload["fulltext_zh"] == record["chunk_text"]
    assert payload["fulltext_zh_segmented"]
