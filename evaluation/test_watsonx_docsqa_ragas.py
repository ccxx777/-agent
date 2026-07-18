"""RAGAS 0.4.3 评分适配器的无网络测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.watsonx_docsqa_ragas import (
    RagasBaselineError,
    build_ragas_summary,
    load_generation_rows,
    metric_arguments,
    selected_input_sha256,
)


def _row(question_id: str = "test_1") -> dict:
    return {
        "question_id": question_id,
        "question": "What is enabled?",
        "answer": "The setting is enabled.",
        "reference_answer": "It is enabled.",
        "contexts": ["The setting is enabled."],
    }


def test_metric_arguments_match_collections_api() -> None:
    row = _row()
    assert metric_arguments("answer_correctness", row) == {
        "user_input": row["question"],
        "response": row["answer"],
        "reference": row["reference_answer"],
    }
    assert metric_arguments("faithfulness", row) == {
        "user_input": row["question"],
        "response": row["answer"],
        "retrieved_contexts": row["contexts"],
    }
    assert metric_arguments("context_relevance", row) == {
        "user_input": row["question"],
        "retrieved_contexts": row["contexts"],
    }


def test_generation_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "details.jsonl"
    row = _row()
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RagasBaselineError, match="重复 question_id"):
        load_generation_rows(path)


def test_selected_signature_ignores_latency_but_tracks_answer() -> None:
    first = _row()
    first["latency_seconds"] = {"total": 1.0}
    second = _row()
    second["latency_seconds"] = {"total": 99.0}
    assert selected_input_sha256([first]) == selected_input_sha256([second])

    second["answer"] = "Different answer"
    assert selected_input_sha256([first]) != selected_input_sha256([second])


def test_summary_reports_metric_coverage_separately() -> None:
    rows = [_row("test_1"), _row("test_2")]
    score_payloads = {
        "test_1": {
            "metrics": {
                "faithfulness": {"value": 1.0, "error": None},
            }
        },
        "test_2": {
            "metrics": {
                "faithfulness": {"value": None, "error": "timeout"},
            }
        },
    }
    summary = build_ragas_summary(
        rows,
        score_payloads,
        metrics=["faithfulness"],
        model="judge",
        base_url="https://example.test",
        ragas_version="0.4.3",
    )

    metric = summary["metrics"]["faithfulness"]
    assert metric["mean"] == 1.0
    assert metric["coverage"] == 0.5
    assert metric["failed_question_ids"] == ["test_2"]
