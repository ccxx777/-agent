#!/usr/bin/env python3
"""追踪 watsonxDocsQA 问题在生产 Cascade Funnel 各阶段的位置。

该诊断器不复制或修改生产召回函数。它在独立 Python 进程中临时包装现有
``_dense_search_scored``、``_sparse_search_scored`` 和
``_bm25_search_scored``，记录 L1 的原始返回值；L2 使用生产模块自己的
``normalize`` 与 ``calculate_query_specificity`` 按当前公式重建；L3 直接采用
``get_final_funnel_top3`` 的真实 Reranker 结果。

所有函数和 collection 常量都会在 ``finally`` 中恢复。运行中的 Backend、
``rag_chunks`` 和自研召回源码均不会被改变。
"""

from __future__ import annotations

import argparse
import asyncio
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

if __package__:
    from .watsonx_docsqa_retrieval_baseline import (
        BaselineError,
        Question,
        _atomic_json,
        _load_runtime_settings,
        evaluation_collection,
        load_questions,
    )
else:  # 直接执行 /app/evaluation/*.py
    from watsonx_docsqa_retrieval_baseline import (  # type: ignore[no-redef]
        BaselineError,
        Question,
        _atomic_json,
        _load_runtime_settings,
        evaluation_collection,
        load_questions,
    )


SEARCH_FUNCTIONS = {
    "dense": "_dense_search_scored",
    "sparse": "_sparse_search_scored",
    "fulltext": "_bm25_search_scored",
}


def _payload(point: Any) -> dict[str, Any]:
    payload = getattr(point, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _point_record(point: Any, *, rank: int, score: float | None = None) -> dict[str, Any]:
    payload = _payload(point)
    result = {
        "rank": rank,
        "point_id": str(getattr(point, "id", "") or ""),
        "doc_id": str(payload.get("doc_id") or ""),
        "chunk_id": str(payload.get("chunk_id") or ""),
        "title": str(payload.get("title") or ""),
        "text_excerpt": str(payload.get("chunk_text") or "")[:500],
    }
    if score is not None:
        result["score"] = float(score)
    return result


@contextmanager
def capture_l1_searches(retriever_module: Any) -> Iterator[dict[str, list[Any]]]:
    """包装三路 L1 函数并在任何退出路径恢复原函数。"""

    originals = {
        path: getattr(retriever_module, function_name)
        for path, function_name in SEARCH_FUNCTIONS.items()
    }
    captured: dict[str, list[Any]] = {}

    for path, function_name in SEARCH_FUNCTIONS.items():
        original = originals[path]

        def wrapper(*args: Any, _path: str = path, _original: Any = original, **kwargs: Any) -> list[Any]:
            result = _original(*args, **kwargs)
            captured[_path] = result
            return result

        setattr(retriever_module, function_name, wrapper)
    try:
        yield captured
    finally:
        for path, function_name in SEARCH_FUNCTIONS.items():
            setattr(retriever_module, function_name, originals[path])


def reconstruct_l2(
    query: str,
    captured: dict[str, list[Any]],
    retriever_module: Any,
) -> tuple[list[tuple[float, Any]], dict[str, float]]:
    """使用生产函数与当前权重公式重建本次调用的 L2 Top-10。"""

    dense_hits = captured.get("dense", [])
    sparse_hits = captured.get("sparse", [])
    fulltext_hits = captured.get("fulltext", [])
    seen: set[str] = set()
    all_lookup: dict[str, Any] = {}
    for _score, point in dense_hits + sparse_hits + fulltext_hits:
        point_id = str(point.id)
        if point_id not in seen:
            seen.add(point_id)
            all_lookup[point_id] = point

    normalized_dense, _ = retriever_module.normalize(dense_hits)
    normalized_sparse, _ = retriever_module.normalize(sparse_hits)
    normalized_fulltext, _ = retriever_module.normalize(fulltext_hits)
    specificity = retriever_module.calculate_query_specificity(query)
    semantic_weight = 1.0 - specificity
    literal_weight = specificity

    final_scores: dict[str, float] = {}
    for point_id in seen:
        dense_score = normalized_dense.get(point_id, 0.0)
        sparse_score = normalized_sparse.get(point_id, 0.0)
        fulltext_score = normalized_fulltext.get(point_id, 0.0)
        base_score = semantic_weight * dense_score + literal_weight * (
            0.5 * sparse_score + 0.5 * fulltext_score
        )
        final_scores[point_id] = max(base_score, 0.5) if dense_score > 0.85 else base_score

    ranked = sorted(final_scores.items(), key=lambda item: item[1], reverse=True)[:10]
    coarse = [(score, all_lookup[point_id]) for point_id, score in ranked]
    weights = {
        "query_specificity": specificity,
        "semantic_weight": semantic_weight,
        "literal_weight": literal_weight,
    }
    return coarse, weights


def first_gold_rank(records: list[dict[str, Any]], gold_doc_ids: set[str]) -> int | None:
    return next(
        (int(item["rank"]) for item in records if item.get("doc_id") in gold_doc_ids),
        None,
    )


def classify_elimination(
    *,
    l1_rank: int | None,
    l2_rank: int | None,
    l3_rank: int | None,
) -> str:
    """返回 Gold 首次消失的 Funnel 阶段。"""

    if l1_rank is None:
        return "L1_NOT_RECALLED"
    if l2_rank is None:
        return "L2_ELIMINATED"
    if l3_rank is None:
        return "L3_RERANKED_OUT"
    return "L3_SURVIVED"


def build_trace(
    question: Question,
    captured: dict[str, list[Any]],
    coarse: list[tuple[float, Any]],
    final_hits: list[Any],
    weights: dict[str, float],
    *,
    collection: str,
    latency_seconds: float,
) -> dict[str, Any]:
    """构造适合人工审计的路径排名与淘汰结论。"""

    path_records = {
        path: [
            _point_record(point, rank=rank, score=score)
            for rank, (score, point) in enumerate(captured.get(path, []), 1)
        ]
        for path in SEARCH_FUNCTIONS
    }
    l1_lookup: dict[str, Any] = {}
    for path in SEARCH_FUNCTIONS:
        for _score, point in captured.get(path, []):
            l1_lookup.setdefault(str(point.id), point)
    l1_records = [
        _point_record(point, rank=rank)
        for rank, point in enumerate(l1_lookup.values(), 1)
    ]
    l2_records = [
        _point_record(point, rank=rank, score=score)
        for rank, (score, point) in enumerate(coarse, 1)
    ]
    l3_records = [
        _point_record(point, rank=rank) for rank, point in enumerate(final_hits, 1)
    ]
    gold = set(question.gold_doc_ids)
    path_gold_ranks = {
        path: first_gold_rank(records, gold) for path, records in path_records.items()
    }
    l1_rank = first_gold_rank(l1_records, gold)
    l2_rank = first_gold_rank(l2_records, gold)
    l3_rank = first_gold_rank(l3_records, gold)
    return {
        "format_version": 1,
        "dataset": "watsonxDocsQA",
        "collection": collection,
        "question_id": question.question_id,
        "question": question.question,
        "reference_answer": question.reference_answer,
        "gold_doc_ids": question.gold_doc_ids,
        "reference_contexts": question.reference_contexts,
        "weights": weights,
        "gold_ranks": {
            "dense": path_gold_ranks["dense"],
            "sparse": path_gold_ranks["sparse"],
            "fulltext": path_gold_ranks["fulltext"],
            "l1_deduplicated": l1_rank,
            "l2_coarse": l2_rank,
            "l3_final": l3_rank,
        },
        "elimination": classify_elimination(
            l1_rank=l1_rank,
            l2_rank=l2_rank,
            l3_rank=l3_rank,
        ),
        "counts": {
            "dense": len(path_records["dense"]),
            "sparse": len(path_records["sparse"]),
            "fulltext": len(path_records["fulltext"]),
            "l1_deduplicated": len(l1_records),
            "l2_coarse": len(l2_records),
            "l3_final": len(l3_records),
        },
        "paths": path_records,
        "l2_coarse": l2_records,
        "l3_final": l3_records,
        "latency_seconds": round(latency_seconds, 6),
    }


async def trace_questions(args: argparse.Namespace) -> list[dict[str, Any]]:
    questions = load_questions(args.questions.resolve())
    lookup = {item.question_id: item for item in questions}
    missing = [question_id for question_id in args.question_ids if question_id not in lookup]
    if missing:
        raise BaselineError(f"问题集不存在这些 question_id：{', '.join(missing)}")

    settings = _load_runtime_settings()
    from app.components.retriever.qdrant.v2_0_0 import main as retriever_module
    from app.infrastructure.embedding_client import EmbeddingClient
    from app.infrastructure.qdrant import QdrantGateway

    gateway = QdrantGateway(settings.qdrant_url)
    qdrant_client = gateway.create_client()
    try:
        collection_info = qdrant_client.get_collection(args.collection)
    except Exception as error:
        raise BaselineError(
            f"无法读取 Qdrant collection：{args.collection}；"
            f"{type(error).__name__}: {error}"
        ) from error
    finally:
        qdrant_client.close()
    points = int(collection_info.points_count or 0)
    if points != args.expected_points:
        raise BaselineError(
            f"collection {args.collection} points={points}，期望 {args.expected_points}"
        )
    if not settings.reranker_api_key:
        raise BaselineError("RERANKER_API_KEY 未配置")

    embedding_client = EmbeddingClient(
        settings.embedding_endpoint,
        timeout=args.embedding_timeout,
    )
    traces: list[dict[str, Any]] = []
    with evaluation_collection(args.collection):
        for question_id in args.question_ids:
            question = lookup[question_id]
            started = time.monotonic()
            dense_vector, sparse_vector = await embedding_client.embed_query(question.question)
            with capture_l1_searches(retriever_module) as captured:
                final_hits = await retriever_module.get_final_funnel_top3(
                    question.question,
                    dense_vec=dense_vector,
                    sparse_dict=sparse_vector,
                    qdrant_url=settings.qdrant_url,
                    reranker_model=settings.reranker_model,
                    reranker_api_url=settings.reranker_api_url,
                    reranker_api_key=settings.reranker_api_key,
                )
            coarse, weights = reconstruct_l2(question.question, captured, retriever_module)
            trace = build_trace(
                question,
                captured,
                coarse,
                final_hits,
                weights,
                collection=args.collection,
                latency_seconds=time.monotonic() - started,
            )
            traces.append(trace)
            print(
                f"{question_id}: {trace['elimination']} "
                f"gold_ranks={trace['gold_ranks']}",
                flush=True,
            )

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(
        output,
        {
            "format_version": 1,
            "dataset": "watsonxDocsQA",
            "collection": args.collection,
            "collection_points": points,
            "traces": traces,
        },
    )
    print(f"诊断明细已写入：{output}", flush=True)
    return traces


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA Funnel 分阶段诊断")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--question-ids", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collection", default="watsonx_docsqa_colab_v1")
    parser.add_argument("--expected-points", type=int, default=6759)
    parser.add_argument("--embedding-timeout", type=float, default=60.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(trace_questions(args))
    except BaselineError as error:
        raise SystemExit(f"[FAIL] {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
