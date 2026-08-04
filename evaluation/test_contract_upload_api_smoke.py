from __future__ import annotations

from evaluation.contract_upload_api_smoke import (
    _parse_expected_redactions,
    _parse_files,
    review_is_terminal,
    validate_public_detail,
    validate_public_payload,
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


def test_public_detail_requires_explicit_external_ocr_opt_in() -> None:
    detail = _detail() | {
        "privacy": _detail()["privacy"] | {"external_raw_image_sent": True}
    }
    errors = validate_public_detail(
        detail,
        privacy_sentinels=[],
        expected_redactions={},
        require_extraction=False,
    )
    assert any("external_raw_image_sent" in error for error in errors)
    assert validate_public_detail(
        detail,
        privacy_sentinels=[],
        expected_redactions={},
        require_extraction=False,
        allow_external_ocr=True,
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


def test_any_public_payload_rejects_private_field_and_sentinel() -> None:
    errors = validate_public_payload(
        {"reviews": [{"storage_path": "/private/review-1", "text": "13912345678"}]},
        privacy_sentinels=["13912345678"],
    )
    assert errors == [
        "public response contains private fields",
        "public response contains privacy sentinel #1",
    ]


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


def test_poll_waits_for_extraction_when_required() -> None:
    detail = _detail() | {"extraction_status": "running"}
    assert not review_is_terminal(detail, require_extraction=True)
    assert review_is_terminal(detail, require_extraction=False)


def test_poll_accepts_extraction_needs_confirmation() -> None:
    detail = _detail() | {"extraction_status": "needs_confirmation"}
    assert review_is_terminal(detail, require_extraction=True)


def test_poll_stops_immediately_when_file_parse_failed() -> None:
    detail = _detail() | {"status": "failed", "extraction_status": "running"}
    assert review_is_terminal(detail, require_extraction=True)
