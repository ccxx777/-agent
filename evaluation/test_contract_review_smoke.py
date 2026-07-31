"""contract_review_smoke 的纯函数测试。"""

from evaluation.contract_review_smoke import summarize_detail, validate_detail


def _detail() -> dict:
    return {
        "review_id": "review-1",
        "filename": "labor.docx",
        "status": "needs_confirmation",
        "extraction_status": "needs_confirmation",
        "confirmation_status": "not_started",
        "quality": {"page_count": 1, "text_coverage": 1.0},
        "privacy": {"redaction_counts": {"phone": 1}},
        "pages": [{"text": "must not appear in summary"}],
        "extraction": {
            "extraction_mode": "single",
            "model_calls": 1,
            "invalid_fact_count": 0,
            "clauses": [{"clause_id": "clause_001"}],
            "facts": [{"field_key": "salary", "value": "8000"}],
            "missing_required_fields": ["housing_fund"],
            "confirmation_questions": ["请确认"],
            "warnings": [],
        },
    }


def test_summary_does_not_include_contract_pages():
    summary = summarize_detail(_detail())

    assert summary["extraction"]["extraction_mode"] == "single"
    assert summary["extraction"]["model_calls"] == 1
    assert summary["extraction"]["facts"] == 1
    assert "pages" not in summary
    assert "must not appear in summary" not in str(summary)


def test_needs_confirmation_is_a_valid_terminal_state():
    assert validate_detail(_detail(), expect_mode="single") == []


def test_invalid_mode_is_reported():
    detail = _detail()
    detail["extraction"]["extraction_mode"] = "unknown"

    errors = validate_detail(detail)

    assert any("extraction_mode 无效" in error for error in errors)
