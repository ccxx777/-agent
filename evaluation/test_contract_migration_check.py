from __future__ import annotations

from evaluation.contract_migration_check import evaluate_schema


def _complete_snapshot() -> tuple[set[str], dict[str, set[str]], set[str]]:
    tables = {"contract_review_tasks", "contract_review_reports", "sessions"}
    columns = {
        "contract_review_tasks": {
            "review_id",
            "session_id",
            "retention_policy",
            "expires_at",
            "deleted_at",
        },
        "contract_review_reports": {
            "report_id",
            "review_id",
            "session_id",
            "report_version",
            "workflow_status",
            "report",
            "assessment_date",
            "input_sha256",
            "report_sha256",
            "legal_corpus_version",
            "rule_version",
            "prompt_version",
            "model_version",
            "parser_version",
        },
        "sessions": {"session_id", "conversation_scope_version", "has_contract_context"},
    }
    indexes = {
        "idx_contract_review_tasks_session_created",
        "idx_contract_review_tasks_expiry",
        "idx_contract_review_reports_review_created",
        "idx_contract_review_reports_session_created",
    }
    return tables, columns, indexes


def test_complete_schema_passes() -> None:
    tables, columns, indexes = _complete_snapshot()

    result = evaluate_schema(tables=tables, columns=columns, indexes=indexes)

    assert result["status"] == "passed"
    assert all(result["checks"].values())


def test_missing_retention_column_blocks_migration_gate() -> None:
    tables, columns, indexes = _complete_snapshot()
    columns["contract_review_tasks"].remove("expires_at")

    result = evaluate_schema(tables=tables, columns=columns, indexes=indexes)

    assert result["status"] == "failed"
    assert result["checks"]["migration_006_retention"] is False
    assert "expires_at" in result["missing_columns"]["contract_review_tasks"]
