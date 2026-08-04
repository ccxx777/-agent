"""法律 A 级资料激活工具的纯单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.legal_labor_activation import (
    ACTIVE_STATUS,
    CONTENT_MATCH_STATUS,
    JURISDICTION,
    OFFICIAL_PUBLIC_SOURCE,
    REVIEW_STATUS,
    ActivationError,
    _activation_fields,
    _pending_field_counts,
    _static_errors,
    _update_markdown,
    activate,
)


def _manifest() -> dict:
    return {"target_collection": "legal_labor_a_v1", "status": "ACTIVE"}


def _metadata(**overrides: object) -> list[dict]:
    record = {
        "source_level": "A",
        "official_url": "https://flk.npc.gov.cn/detail?id=example",
        "official_status_code": 3,
        "amendment_or_repeal_status": "有效",
        "effective_date": "2013-07-01",
        "jurisdiction": JURISDICTION,
        "national_applicability": True,
        "license_status": OFFICIAL_PUBLIC_SOURCE,
    }
    record.update(overrides)
    return [record]


def test_activation_fields_are_explicit_and_auditable() -> None:
    fields = _activation_fields(reviewer="ccxx", reviewed_at="2026-08-04T00:00:00+00:00")

    assert fields == {
        "jurisdiction": JURISDICTION,
        "national_applicability": True,
        "license_status": OFFICIAL_PUBLIC_SOURCE,
        "content_match_status": CONTENT_MATCH_STATUS,
        "review_status": REVIEW_STATUS,
        "reviewed_by": "ccxx",
        "reviewed_at": "2026-08-04T00:00:00+00:00",
        "legal_activation_status": ACTIVE_STATUS,
    }


def test_pending_field_counts_only_reports_unresolved_metadata() -> None:
    metadata = _metadata(
        jurisdiction="PENDING_MANUAL_VERIFICATION",
        license_status=None,
    )

    assert _pending_field_counts(metadata) == {
        "jurisdiction": 1,
        "license_status": 1,
    }


def test_static_checks_reject_protected_collection_and_invalid_provenance() -> None:
    errors = _static_errors(
        manifest=_manifest(),
        metadata=_metadata(
            source_level="B",
            official_url="https://example.com/not-official",
            official_status_code=2,
            amendment_or_repeal_status="已废止",
            effective_date="unknown",
        ),
        chunks=[{"point_id": "p1"}],
        collection="rag_chunks",
    )

    assert len(errors) == 7
    assert any("受保护" in error for error in errors)


def test_update_markdown_preserves_body_and_updates_front_matter(tmp_path: Path) -> None:
    path = tmp_path / "law.md"
    path.write_text(
        "---json\n"
        + json.dumps({"legal_activation_status": "PENDING_LEGAL_REVIEW"})
        + "\n---\n\n# 正文\n\n第一条。\n",
        encoding="utf-8",
    )

    digest = _update_markdown(path, {"legal_activation_status": ACTIVE_STATUS})
    content = path.read_text(encoding="utf-8")

    payload = content.split("---json\n", 1)[1].split("\n---", 1)[0]
    assert json.loads(payload)["legal_activation_status"] == ACTIVE_STATUS
    assert "# 正文" in content and "第一条。" in content
    assert len(digest) == 64


def test_activate_requires_all_human_confirmations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "evaluation.legal_labor_activation._bundle",
        lambda base, prepared: {
            "base": base,
            "prepared": prepared,
            "manifest": _manifest(),
            "metadata": _metadata(),
            "articles": [{"article_id": "a1"}],
            "chunks": [{"point_id": "p1"}],
            "paths": {},
            "metadata_path": base / "metadata.jsonl",
        },
    )

    with pytest.raises(ActivationError, match="confirm"):
        activate(
            base=Path("."),
            prepared=Path("."),
            qdrant_url="http://127.0.0.1:6333",
            collection="legal_labor_a_v1",
            reviewer="ccxx",
            review_note="",
            confirm_national_scope=False,
            confirm_effective_status=True,
            confirm_content_match=True,
            timeout=1.0,
            apply=False,
        )
