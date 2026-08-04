"""Tests for the fixed A-level legal retrieval smoke gate."""

from __future__ import annotations

from types import SimpleNamespace

from evaluation.legal_retrieval_smoke import (
    DEFAULT_QUESTION_FILE,
    _document_summary,
    _load_question_cases,
    _validate_documents,
)


def _document(*, pending: bool = True, excerpt: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        rank=1,
        doc_id="labor_contract_law_20121228",
        chunk_id="labor_contract_law_20121228/article_010",
        title="中华人民共和国劳动合同法",
        source="https://flk.npc.gov.cn/detail?id=law",
        text=excerpt or "第十条 建立劳动关系，应当订立书面劳动合同。已建立劳动关系的，一个月内订立。",
        context_text=excerpt
        or "第十条 建立劳动关系，应当订立书面劳动合同。已建立劳动关系的，一个月内订立。",
        metadata={
            "source_level": "A",
            "citation_eligible": True,
            "citation_label": "《中华人民共和国劳动合同法》第十条",
            "article_no": "第十条",
            "effective_date": "2013-07-01",
            "official_url": "https://flk.npc.gov.cn/detail?id=law",
            "legal_activation_status": "PENDING_LEGAL_REVIEW" if pending else "ACTIVE",
        },
    )


def _expected() -> dict:
    return {
        "doc_id": "labor_contract_law_20121228",
        "article_no": "第十条",
        "effective_date": "2013-07-01",
        "official_url_prefix": "https://flk.npc.gov.cn/",
        "required_terms": ["书面劳动合同", "一个月"],
    }


def test_fixed_question_file_contains_ten_structured_questions() -> None:
    cases = _load_question_cases(DEFAULT_QUESTION_FILE)

    assert len(cases) == 10
    assert len({case["question_id"] for case in cases}) == 10
    assert all(case["expected"]["required_terms"] for case in cases)


def test_document_validation_checks_article_source_date_and_excerpt() -> None:
    errors = _validate_documents(
        [_document()],
        expected=_expected(),
        allow_pending_governance=True,
    )

    assert errors == []
    summary = _document_summary(_document())
    assert summary["article_no"] == "第十条"
    assert "书面劳动合同" in summary["citation_excerpt"]


def test_pending_governance_requires_explicit_staging_flag() -> None:
    errors = _validate_documents(
        [_document(pending=True)],
        expected=_expected(),
        allow_pending_governance=False,
    )

    assert any("尚未 ACTIVE" in error for error in errors)


def test_document_validation_rejects_wrong_date_and_citation_terms() -> None:
    document = _document(excerpt="第十条 仅有书面劳动合同")
    document.metadata["effective_date"] = "2000-01-01"

    errors = _validate_documents(
        [document],
        expected=_expected(),
        allow_pending_governance=True,
    )

    assert any("effective_date" in error for error in errors)
    assert any("一个月" in error for error in errors)
