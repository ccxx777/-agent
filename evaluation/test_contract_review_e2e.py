"""Pure tests for the contract review end-to-end acceptance gate."""

from __future__ import annotations

from io import BytesIO

from evaluation.contract_review_e2e import (
    ContractReviewE2EError,
    _confirmation_items,
    _external_ocr_expectation_error,
    _extract_pdf_text_for_privacy,
    _poll_review,
    _privacy_errors,
    _require_test_confirmation_acknowledgement,
    _review_id_from_upload,
    summarize_report,
)


def _confirmation(*facts: dict, unresolved: list[str]) -> dict:
    return {
        "confirmation_revision": 3,
        "facts": list(facts),
        "unresolved_questions": [
            {"fact_id": fact_id, "question": f"confirm {fact_id}"}
            for fact_id in unresolved
        ],
    }


def test_confirmation_items_prefers_supplement_for_unresolved_facts() -> None:
    payload = _confirmation(
        {
            "fact_id": "work_location",
            "allowed_actions": ["supplement", "not_applicable", "defer"],
        },
        {
            "fact_id": "salary",
            "allowed_actions": ["confirm", "supplement"],
        },
        unresolved=["work_location", "salary"],
    )

    items, counts = _confirmation_items(
        payload,
        policy="supplement",
        supplement_value="测试地点",
    )

    assert items == [
        {
            "fact_id": "work_location",
            "action": "supplement",
            "value": "测试地点",
            "note": "端到端测试自动补充，不代表用户事实",
        },
        {"fact_id": "salary", "action": "confirm"},
    ]
    assert counts == {"supplement": 1, "confirm": 1}


def test_confirmation_items_can_mark_unresolved_fact_not_applicable() -> None:
    payload = _confirmation(
        {
            "fact_id": "housing_fund",
            "allowed_actions": ["not_applicable", "defer"],
        },
        unresolved=["housing_fund"],
    )

    items, counts = _confirmation_items(
        payload,
        policy="not_applicable",
        supplement_value="ignored",
    )

    assert items == [
        {
            "fact_id": "housing_fund",
            "action": "not_applicable",
            "note": "端到端测试标记，不代表真实合同判断",
        }
    ]
    assert counts == {"not_applicable": 1}


def test_confirmation_items_does_not_auto_defer() -> None:
    payload = _confirmation(
        {"fact_id": "leave", "allowed_actions": ["defer"]},
        unresolved=["leave"],
    )

    try:
        _confirmation_items(
            payload,
            policy="supplement",
            supplement_value="ignored",
        )
    except ContractReviewE2EError as error:
        assert "没有可用的自动化确认动作" in str(error)
    else:  # pragma: no cover - defensive assertion for a gate safety invariant
        raise AssertionError("defer must not be selected by the automated gate")


def test_confirmation_items_exercises_confirm_when_everything_is_resolved() -> None:
    payload = _confirmation(
        {"fact_id": "employer", "allowed_actions": ["confirm"]},
        unresolved=[],
    )

    items, counts = _confirmation_items(
        payload,
        policy="supplement",
        supplement_value="ignored",
    )

    assert items == [{"fact_id": "employer", "action": "confirm"}]
    assert counts == {"confirm": 1}


def test_summarize_report_is_metadata_only() -> None:
    report = {
        "report_id": "report-1",
        "review_id": "review-1",
        "session_id": "session-1",
        "workflow_status": "partial",
        "findings": [{"summary": "private finding"}],
        "legal_sources": [{"quote": "private legal quote"}],
        "case_sources": [],
        "pending_questions": ["private question"],
        "disclaimer": "private report text",
    }

    summary = summarize_report(report)

    assert summary == {
        "report_id": "report-1",
        "review_id": "review-1",
        "session_id": "session-1",
        "workflow_status": "partial",
        "findings": 1,
        "legal_sources": 1,
        "case_sources": 0,
        "pending_questions": 1,
    }
    assert "private finding" not in str(summary)
    assert "disclaimer" not in summary


def test_privacy_errors_detect_private_fields_and_sentinels() -> None:
    errors = _privacy_errors(
        {"review_id": "r1", "storage_path": "/private/file", "answer": "ok"},
        ["secret-value"],
    )
    assert "public response contains private fields" in errors

    sentinel_errors = _privacy_errors(
        {"review_id": "r1", "answer": "secret-value"},
        ["secret-value"],
    )
    assert "public response contains privacy sentinel #1" in sentinel_errors


def test_upload_review_id_is_extracted_before_privacy_validation() -> None:
    assert _review_id_from_upload(
        {"review_id": "review-1", "storage_path": "/private/contract"}
    ) == "review-1"

    try:
        _review_id_from_upload({"storage_path": "/private/contract"})
    except ContractReviewE2EError as error:
        assert "清理句柄" in str(error)
    else:  # pragma: no cover - defensive assertion for cleanup safety
        raise AssertionError("an upload without review_id must fail explicitly")


def test_confirmation_write_requires_explicit_acknowledgement() -> None:
    try:
        _require_test_confirmation_acknowledgement(False)
    except ContractReviewE2EError as error:
        assert "--ack-test-confirmation-writes" in str(error)
    else:  # pragma: no cover - defensive assertion for write safety
        raise AssertionError("confirmation writes must require explicit acknowledgement")

    _require_test_confirmation_acknowledgement(True)


def test_external_ocr_expectation_is_stricter_than_allowance() -> None:
    detail = {"privacy": {"external_raw_image_sent": False}}
    assert _external_ocr_expectation_error(detail, expect_external_ocr=False) is None
    assert "不为 true" in str(
        _external_ocr_expectation_error(detail, expect_external_ocr=True)
    )
    assert _external_ocr_expectation_error(
        {"privacy": {"external_raw_image_sent": True}},
        expect_external_ocr=True,
    ) is None


def test_poll_fails_fast_when_extraction_never_starts() -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "review_id": "review-1",
                "status": "ready",
                "extraction_status": "not_started",
            }

    class Client:
        def get(self, *_args, **_kwargs) -> Response:
            return Response()

    try:
        _poll_review(
            Client(),  # type: ignore[arg-type]
            base_url="http://test",
            headers={},
            review_id="review-1",
            poll_seconds=0.01,
            timeout_seconds=1,
            sentinels=[],
            allow_external_ocr=False,
            expect_external_ocr=False,
        )
    except ContractReviewE2EError as error:
        assert "事实提取未启动" in str(error)
    else:  # pragma: no cover - defensive assertion for startup diagnostics
        raise AssertionError("not_started extraction must fail fast")


def test_pdf_privacy_check_extracts_encoded_visible_text() -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length 54 >>\nstream\nBT /F1 12 Tf 72 720 Td "
            b"(secret-value) Tj ET\nendstream"
        ),
    ]
    buffer = BytesIO(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode())
        buffer.write(obj)
        buffer.write(b"\nendobj\n")
    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode())
    buffer.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )

    text = _extract_pdf_text_for_privacy(buffer.getvalue())

    assert "secret-value" in text
