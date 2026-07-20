"""检索迁移Hit@3质量门测试。"""

from __future__ import annotations

import pytest

from evaluation.compare_retrieval_baselines import ComparisonError, compare


def _summary(collection: str, hit3: float, latency: float) -> dict:
    return {
        "collection": collection,
        "total_questions": 30,
        "metrics": {
            "hit_at_1": hit3 - 0.1,
            "hit_at_3": hit3,
            "mrr_at_3": hit3 - 0.05,
            "mean_recall_at_3": hit3,
        },
        "latency_seconds": {"mean": latency},
        "zero_hit_question_ids": [],
    }


def test_compare_accepts_equal_hit3_and_reports_speedup() -> None:
    report = compare(
        _summary("old", 0.933333, 4.0),
        _summary("new", 0.933333, 1.0),
        tolerance=0.0,
    )
    assert report["status"] == "passed"
    assert report["mean_latency_seconds"]["speedup"] == 4.0


def test_compare_rejects_hit3_regression() -> None:
    with pytest.raises(ComparisonError, match="failed"):
        compare(
            _summary("old", 0.933333, 4.0),
            _summary("new", 0.9, 1.0),
            tolerance=0.0,
        )
