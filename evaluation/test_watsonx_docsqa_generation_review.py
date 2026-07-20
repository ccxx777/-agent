"""30题生成统计、重点抽查与人工确认质量门的离线测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.watsonx_docsqa_generation_review import (
    ReviewError,
    approve_review,
    build_review,
    load_generation_rows,
    validate_approval,
    write_review_artifacts,
)


def _row(index: int, *, gold_hit: bool = True, refusal: bool = False) -> dict:
    question_id = f"test_{index}"
    answer = (
        "抱歉，知识库中未找到相关信息 [1]。"
        if refusal
        else f"这是第{index}题的完整答案，所有关键陈述都有证据支持 [1]。"
    )
    return {
        "question_id": question_id,
        "question": f"What is item {index}?",
        "reference_answer": f"Reference {index}",
        "gold_doc_ids": [f"gold-{index}"],
        "answer": answer,
        "contexts": ["evidence one", "evidence two", "evidence three"],
        "documents": [
            {
                "rank": rank,
                "doc_id": f"gold-{index}" if gold_hit and rank == 1 else f"other-{index}-{rank}",
                "title": f"Document {rank}",
                "source": "https://example.test",
            }
            for rank in range(1, 4)
        ],
        "latency_seconds": {
            "retrieval": float(index),
            "generation": 1.0,
            "total": float(index + 1),
        },
    }


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_review_prioritizes_gold_miss_and_refusal() -> None:
    rows = [
        _row(1),
        _row(2, gold_hit=False, refusal=True),
        _row(3),
    ]
    summary, spotcheck = build_review(
        rows,
        expected_questions=3,
        spotcheck_target=2,
    )

    assert summary["retrieval"]["hit_at_3"] == pytest.approx(2 / 3, abs=1e-6)
    assert summary["retrieval"]["zero_hit_question_ids"] == ["test_2"]
    assert summary["answers"]["refusal_count"] == 1
    selected = {item["question_id"]: item for item in spotcheck["samples"]}
    assert "test_2" in selected
    assert "gold_not_in_top3" in selected["test_2"]["selection_reasons"]
    assert "refusal_answer" in selected["test_2"]["selection_reasons"]


def test_loader_requires_complete_fixed_ids(tmp_path: Path) -> None:
    path = tmp_path / "details.jsonl"
    _write_rows(path, [_row(1), _row(3)])

    with pytest.raises(ReviewError, match="题目 ID 不完整"):
        load_generation_rows(path, expected_questions=2)


def test_approval_is_invalid_when_generation_changes(tmp_path: Path) -> None:
    generations = tmp_path / "details.jsonl"
    review_dir = tmp_path / "review"
    rows = [_row(1), _row(2), _row(3)]
    _write_rows(generations, rows)
    loaded = load_generation_rows(generations, expected_questions=3)
    write_review_artifacts(
        loaded,
        review_dir,
        expected_questions=3,
        spotcheck_target=2,
    )
    approval = approve_review(review_dir, reviewer="tester", note="checked")
    assert approval["valid"] is True
    validate_approval(generations, review_dir, expected_questions=3)

    rows[0]["answer"] = "答案已经变化 [1]。"
    _write_rows(generations, rows)
    with pytest.raises(ReviewError, match="生成答案已变化"):
        validate_approval(generations, review_dir, expected_questions=3)
