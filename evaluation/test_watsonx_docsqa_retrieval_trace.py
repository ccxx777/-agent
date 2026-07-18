"""Funnel 分阶段诊断器的离线测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evaluation.watsonx_docsqa_retrieval_trace import (
    capture_l1_searches,
    classify_elimination,
    first_gold_rank,
    reconstruct_l2,
)


@pytest.mark.parametrize(
    ("ranks", "expected"),
    [
        ((None, None, None), "L1_NOT_RECALLED"),
        ((2, None, None), "L2_ELIMINATED"),
        ((2, 4, None), "L3_RERANKED_OUT"),
        ((2, 4, 1), "L3_SURVIVED"),
    ],
)
def test_classifies_first_elimination_stage(
    ranks: tuple[int | None, int | None, int | None],
    expected: str,
) -> None:
    assert classify_elimination(
        l1_rank=ranks[0],
        l2_rank=ranks[1],
        l3_rank=ranks[2],
    ) == expected


def test_first_gold_rank_uses_document_id() -> None:
    records = [
        {"rank": 1, "doc_id": "noise"},
        {"rank": 2, "doc_id": "gold"},
    ]
    assert first_gold_rank(records, {"gold"}) == 2


def test_reconstruct_l2_keeps_production_semantic_safety_floor() -> None:
    point_a = SimpleNamespace(id="a", payload={"doc_id": "a"})
    point_b = SimpleNamespace(id="b", payload={"doc_id": "b"})
    captured = {
        "dense": [(0.9, point_a), (0.5, point_b)],
        "sparse": [(0.1, point_a), (0.9, point_b)],
        "fulltext": [],
    }

    def normalize(hits):
        scores = [score for score, _point in hits]
        if not scores:
            return {}, {}
        denominator = max(scores) - min(scores) + 1e-6
        return (
            {
                str(point.id): (score - min(scores)) / denominator
                for score, point in hits
            },
            {},
        )

    module = SimpleNamespace(
        normalize=normalize,
        calculate_query_specificity=lambda _query: 0.8,
    )
    coarse, weights = reconstruct_l2("query", captured, module)

    assert [point.id for _score, point in coarse] == ["a", "b"]
    assert coarse[0][0] == 0.5
    assert coarse[1][0] == pytest.approx(0.4, abs=1e-6)
    assert weights == {
        "query_specificity": 0.8,
        "semantic_weight": pytest.approx(0.2),
        "literal_weight": 0.8,
    }


def test_capture_restores_all_search_functions_after_error() -> None:
    def dense() -> list[str]:
        return ["dense"]

    def sparse() -> list[str]:
        return ["sparse"]

    def fulltext() -> list[str]:
        return ["fulltext"]

    module = SimpleNamespace(
        _dense_search_scored=dense,
        _sparse_search_scored=sparse,
        _fulltext_search_scored=fulltext,
    )

    with pytest.raises(RuntimeError, match="stop"):
        with capture_l1_searches(module) as captured:
            assert module._dense_search_scored() == ["dense"]
            assert captured["dense"] == ["dense"]
            raise RuntimeError("stop")

    assert module._dense_search_scored is dense
    assert module._sparse_search_scored is sparse
    assert module._fulltext_search_scored is fulltext
