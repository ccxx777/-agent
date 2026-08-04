from __future__ import annotations

from evaluation.contract_upload_api_smoke import (
    _parse_expected_redactions,
    _parse_files,
    validate_public_detail,
)


def _detail() -> dict:
    return {
        "review_id": "review-1",
        "session_id": "session-1",
        "retention_policy": "short",
        "expires_at": "2026-08-11T00:00:00",
        "status": "ready",
        "extraction_status": "not_started",
        "privacy": {
            "redaction_counts": {"phone": 1},
            "zero_width_sequences_detected": 0,
            "external_raw_image_sent": False,
        },
    }


def test_public_detail_accepts_redacted_response() -> None:
    assert validate_public_detail(
        _detail(),
        privacy_sentinels=["13912345678"],
        expected_redactions={"phone": 1},
        require_extraction=False,
    ) == []


def test_public_detail_rejects_private_field_and_sentinel() -> None:
    detail = _detail() | {"storage_path": "/private/contracts/review-1"}
    detail["pages"] = [{"text": "13912345678"}]

    errors = validate_public_detail(
        detail,
        privacy_sentinels=["13912345678"],
        expected_redactions={},
        require_extraction=False,
    )

    assert "公共响应包含私有字段" in errors
    assert any(error.startswith("公共响应包含第") for error in errors)


def test_file_parser_requires_all_three_formats(tmp_path) -> None:
    paths = [tmp_path / name for name in ("a.pdf", "b.doc", "c.docx")]
    for path in paths:
        path.write_bytes(b"test")

    cases = _parse_files([f"{path.suffix.removeprefix('.')}={path}" for path in paths])

    assert {label for label, _ in cases} == {"pdf", "doc", "docx"}


def test_redaction_expectation_parser() -> None:
    assert _parse_expected_redactions(["phone=1", "id_card=0"]) == {
        "phone": 1,
        "id_card": 0,
    }
