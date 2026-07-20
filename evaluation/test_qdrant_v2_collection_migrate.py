"""Qdrant v2离线迁移器的纯计算测试。"""

from __future__ import annotations

import math

from evaluation.qdrant_v2_collection_migrate import build_bm25_corpus
from app.services.query_specificity import english_tokens, sparse_token_id


def test_bm25_vectors_rank_repeated_rare_term_higher() -> None:
    corpus = build_bm25_corpus(
        ["alpha alpha common", "beta common", "common common"],
        english_tokens,
    )
    alpha_id = sparse_token_id("alpha")
    first_indices, first_values = corpus.vectors[0]
    second_indices, _ = corpus.vectors[1]

    assert alpha_id in first_indices
    assert alpha_id not in second_indices
    assert first_values[first_indices.index(alpha_id)] > 0
    assert corpus.vocabulary_size == 3
    assert corpus.hash_collisions == 0
    assert math.isfinite(corpus.average_document_length)


def test_bm25_vector_indices_are_sorted_and_aligned() -> None:
    corpus = build_bm25_corpus(["zeta alpha beta"], english_tokens)
    indices, values = corpus.vectors[0]
    assert indices == sorted(indices)
    assert len(indices) == len(values) == 3
