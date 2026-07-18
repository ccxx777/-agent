"""watsonxDocsQA 纯召回基线的离线契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.watsonx_docsqa_retrieval_baseline import (
    BaselineError,
    Question,
    build_summary,
    evaluation_collection,
    load_existing_results,
    load_questions,
    score_retrieval,
)


def _question(question_id: str = "q-1") -> Question:
    return Question(
        question_id=question_id,
        question="What is the setting?",
        reference_answer="Enabled.",
        gold_doc_ids=["gold-a", "gold-b"],
        reference_contexts=["The setting is enabled."],
    )


def test_score_retrieval_uses_funnel_chunk_rank_and_unique_gold_docs() -> None:
    metrics = score_retrieval(
        _question(),
        [
            {"doc_id": "noise"},
            {"doc_id": "gold-a"},
            {"doc_id": "gold-a"},
        ],
    )

    assert metrics == {
        "hit_at_1": False,
        "hit_at_3": True,
        "reciprocal_rank_at_3": 0.5,
        "recall_at_3": 0.5,
        "first_relevant_rank": 2,
        "matched_gold_doc_ids": ["gold-a"],
    }


def test_load_questions_rejects_duplicate_question_ids(tmp_path: Path) -> None:
    path = tmp_path / "test.jsonl"
    row = {
        "question_id": "q-1",
        "question": "Question",
        "reference_answer": "Answer",
        "gold_doc_ids": ["doc-1"],
        "reference_contexts": ["Context"],
    }
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BaselineError, match="重复 question_id"):
        load_questions(path)


def test_existing_results_must_match_current_gold(tmp_path: Path) -> None:
    path = tmp_path / "details.jsonl"
    path.write_text(
        json.dumps(
            {
                "question_id": "q-1",
                "question": "What is the setting?",
                "gold_doc_ids": ["different"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BaselineError, match="不一致"):
        load_existing_results(path, {"q-1": _question()}, collection="benchmark")


def test_summary_counts_errors_as_misses() -> None:
    questions = [_question("q-1"), _question("q-2")]
    results = {
        "q-1": {
            "question_id": "q-1",
            "metrics": {
                "hit_at_1": True,
                "hit_at_3": True,
                "reciprocal_rank_at_3": 1.0,
                "recall_at_3": 0.5,
            },
            "latency_seconds": 1.0,
            "error": None,
        },
        "q-2": {
            "question_id": "q-2",
            "metrics": {
                "hit_at_1": False,
                "hit_at_3": False,
                "reciprocal_rank_at_3": 0.0,
                "recall_at_3": 0.0,
            },
            "latency_seconds": 3.0,
            "error": "TimeoutError",
        },
    }

    summary = build_summary(questions, results, collection="benchmark")

    assert summary["error_questions"] == 1
    assert summary["metrics"] == {
        "hit_at_1": 0.5,
        "hit_at_3": 0.5,
        "mrr_at_3": 0.5,
        "mean_recall_at_3": 0.25,
    }
    assert summary["latency_seconds"]["mean"] == 2.0
    assert summary["zero_hit_question_ids"] == ["q-2"]


def test_evaluation_collection_restores_production_default() -> None:
    from app.components.retriever.qdrant.v2_0_0 import main as retriever_module

    original = retriever_module.COLLECTION_NAME
    with evaluation_collection("benchmark"):
        assert retriever_module.COLLECTION_NAME == "benchmark"
    assert retriever_module.COLLECTION_NAME == original
