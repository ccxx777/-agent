#!/usr/bin/env python3
"""在 watsonxDocsQA 固定问题集上评测现有 Cascade Funnel。

本脚本只评测检索，不调用答案生成模型，也不依赖 RAGAS。它直接复用生产环境的
``RetrievalService``，因此 Query Embedding、三路召回、粗排与 Reranker 均与
Agent 主链一致。

生产 Funnel 目前把 collection 固定为 ``rag_chunks``。为了不修改用户自研召回
算法，本脚本仅在当前独立评测进程内临时切换该模块常量；进程退出时恢复原值，
不会改变正在运行的 Backend，也不会影响生产 collection。

输出包括：

* ``details.jsonl``：逐题 Gold 文档、召回结果、命中排名和延迟；
* ``summary.json``：Hit@1、Hit@3、MRR@3、Recall@3 与失败问题清单。

逐题结果采用追加写入。任务中断后重新执行相同命令会跳过已完成 question_id，
继续剩余问题；已有问题的题目或 Gold 文档发生变化时则拒绝混用结果。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if BACKEND_ROOT.is_dir() and str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DETAILS_FILENAME = "details.jsonl"
SUMMARY_FILENAME = "summary.json"


class BaselineError(RuntimeError):
    """输入数据、已有结果或运行配置不满足可比较评测条件。"""


@dataclass(frozen=True)
class Question:
    """标准化后的固定问题与文档级 Gold。"""

    question_id: str
    question: str
    reference_answer: str
    gold_doc_ids: list[str]
    reference_contexts: list[str]


def _required_text(value: Any, *, field: str, row_number: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise BaselineError(f"test.jsonl 第 {row_number} 行的 {field} 为空")
    return text


def _string_list(value: Any, *, field: str, row_number: int) -> list[str]:
    if not isinstance(value, list):
        raise BaselineError(f"test.jsonl 第 {row_number} 行的 {field} 必须是数组")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise BaselineError(f"test.jsonl 第 {row_number} 行的 {field} 为空")
    return result


def load_questions(path: Path, limit: int | None = None) -> list[Question]:
    """读取 prepared/test.jsonl，并拒绝重复 ID 或不完整 Gold。"""

    if not path.is_file():
        raise BaselineError(f"问题集不存在：{path}")
    if limit is not None and limit <= 0:
        raise BaselineError("limit 必须大于 0")

    questions: list[Question] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for row_number, raw_line in enumerate(source, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise BaselineError(
                    f"test.jsonl 第 {row_number} 行不是合法 JSON"
                ) from error
            question_id = _required_text(
                row.get("question_id"), field="question_id", row_number=row_number
            )
            if question_id in seen:
                raise BaselineError(f"test.jsonl 存在重复 question_id：{question_id}")
            seen.add(question_id)
            questions.append(
                Question(
                    question_id=question_id,
                    question=_required_text(
                        row.get("question"), field="question", row_number=row_number
                    ),
                    reference_answer=_required_text(
                        row.get("reference_answer"),
                        field="reference_answer",
                        row_number=row_number,
                    ),
                    gold_doc_ids=_string_list(
                        row.get("gold_doc_ids"),
                        field="gold_doc_ids",
                        row_number=row_number,
                    ),
                    reference_contexts=_string_list(
                        row.get("reference_contexts"),
                        field="reference_contexts",
                        row_number=row_number,
                    ),
                )
            )
            if limit is not None and len(questions) >= limit:
                break
    if not questions:
        raise BaselineError("问题集没有可评测记录")
    return questions


def score_retrieval(question: Question, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """按 Funnel 返回的 chunk 排名计算文档级检索指标。"""

    gold = set(question.gold_doc_ids)
    retrieved_doc_ids = [str(item.get("doc_id") or "") for item in documents]
    first_relevant_rank = next(
        (rank for rank, doc_id in enumerate(retrieved_doc_ids, 1) if doc_id in gold),
        None,
    )
    top3_gold = gold.intersection(retrieved_doc_ids[:3])
    return {
        "hit_at_1": bool(retrieved_doc_ids[:1] and retrieved_doc_ids[0] in gold),
        "hit_at_3": bool(top3_gold),
        "reciprocal_rank_at_3": (
            1.0 / first_relevant_rank
            if first_relevant_rank is not None and first_relevant_rank <= 3
            else 0.0
        ),
        "recall_at_3": len(top3_gold) / len(gold),
        "first_relevant_rank": first_relevant_rank,
        "matched_gold_doc_ids": sorted(top3_gold),
    }


def _result_document(document: Any) -> dict[str, Any]:
    """保留定位误召回所需字段，避免把完整 chunk 重复写入结果。"""

    if hasattr(document, "model_dump"):
        item = document.model_dump()
    elif isinstance(document, dict):
        item = document
    else:
        raise BaselineError("Funnel 返回了无法序列化的 document")
    return {
        "rank": int(item.get("rank") or 0),
        "point_id": str(item.get("point_id") or ""),
        "doc_id": str(item.get("doc_id") or ""),
        "chunk_id": str(item.get("chunk_id") or ""),
        "title": str(item.get("title") or ""),
        "source": str(item.get("source") or ""),
        "context_text": str(item.get("context_text") or ""),
        "qdrant_score": item.get("qdrant_score"),
    }


def load_existing_results(
    path: Path,
    questions: dict[str, Question],
    *,
    collection: str,
) -> dict[str, dict[str, Any]]:
    """加载断点结果，并确认它仍属于当前固定问题集。"""

    if not path.exists():
        return {}
    existing: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for row_number, raw_line in enumerate(source, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise BaselineError(
                    f"已有 details.jsonl 第 {row_number} 行损坏"
                ) from error
            question_id = str(row.get("question_id") or "")
            expected = questions.get(question_id)
            if expected is None:
                raise BaselineError(f"已有结果包含当前问题集之外的 ID：{question_id}")
            if row.get("question") != expected.question or row.get("gold_doc_ids") != expected.gold_doc_ids:
                raise BaselineError(f"已有结果与当前问题集不一致：{question_id}")
            if row.get("collection") != collection:
                raise BaselineError(
                    f"已有结果 collection={row.get('collection')}，当前为 {collection}"
                )
            if question_id in existing:
                raise BaselineError(f"已有结果重复 question_id：{question_id}")
            existing[question_id] = row
    return existing


def _percentile(values: list[float], fraction: float) -> float:
    """使用 nearest-rank，避免为 30 条基线引入额外统计依赖。"""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def build_summary(
    questions: list[Question],
    results: dict[str, dict[str, Any]],
    *,
    collection: str,
) -> dict[str, Any]:
    """错误按未命中计入分母，避免失败请求虚高平均分。"""

    ordered = [results[item.question_id] for item in questions if item.question_id in results]
    total = len(questions)
    errors = [row for row in ordered if row.get("error")]
    latencies = [float(row.get("latency_seconds") or 0.0) for row in ordered]
    hit1 = sum(bool(row.get("metrics", {}).get("hit_at_1")) for row in ordered)
    hit3 = sum(bool(row.get("metrics", {}).get("hit_at_3")) for row in ordered)
    reciprocal_ranks = [
        float(row.get("metrics", {}).get("reciprocal_rank_at_3") or 0.0)
        for row in ordered
    ]
    recalls = [
        float(row.get("metrics", {}).get("recall_at_3") or 0.0) for row in ordered
    ]
    zero_hit_ids = [
        row["question_id"]
        for row in ordered
        if not row.get("metrics", {}).get("hit_at_3")
    ]
    denominator = total or 1
    return {
        "format_version": 1,
        "dataset": "watsonxDocsQA",
        "split": "test",
        "collection": collection,
        "funnel": "production Cascade Funnel, final Top-3",
        "generated_at": datetime.now(UTC).isoformat(),
        "total_questions": total,
        "completed_questions": len(ordered),
        "error_questions": len(errors),
        "metrics": {
            "hit_at_1": round(hit1 / denominator, 6),
            "hit_at_3": round(hit3 / denominator, 6),
            "mrr_at_3": round(sum(reciprocal_ranks) / denominator, 6),
            "mean_recall_at_3": round(sum(recalls) / denominator, 6),
        },
        "latency_seconds": {
            "mean": round(statistics.fmean(latencies), 6) if latencies else 0.0,
            "p50": round(_percentile(latencies, 0.50), 6),
            "p95": round(_percentile(latencies, 0.95), 6),
        },
        "zero_hit_question_ids": zero_hit_ids,
        "error_question_ids": [row["question_id"] for row in errors],
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


@contextmanager
def evaluation_collection(collection: str) -> Iterator[None]:
    """只改变当前评测进程使用的 Funnel collection，随后恢复。"""

    if not collection.strip():
        raise BaselineError("collection 不能为空")
    from app.components.retriever.qdrant.v2_0_0 import main as retriever_module

    original = retriever_module.COLLECTION_NAME
    retriever_module.COLLECTION_NAME = collection
    try:
        yield
    finally:
        retriever_module.COLLECTION_NAME = original


def _load_runtime_settings() -> Any:
    """与 Backend 启动顺序一致：先加载 /app/.env，再实例化 Settings。"""

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", "/app/.env"), override=True)
    from app.config import Settings

    return Settings()


async def run_baseline(args: argparse.Namespace) -> dict[str, Any]:
    """顺序执行固定问题，降低 CPU Embedding 与外部 Reranker 的抖动。"""

    questions = load_questions(args.questions.resolve(), args.limit)
    question_lookup = {item.question_id: item for item in questions}
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    details_path = output_dir / DETAILS_FILENAME
    summary_path = output_dir / SUMMARY_FILENAME
    results = load_existing_results(
        details_path,
        question_lookup,
        collection=args.collection,
    )

    settings = _load_runtime_settings()
    from app.infrastructure.embedding_client import EmbeddingClient
    from app.infrastructure.qdrant import QdrantGateway
    from app.services.retrieval_service import RetrievalService

    qdrant = QdrantGateway(settings.qdrant_url)
    qdrant_client = qdrant.create_client()
    try:
        collection_info = qdrant_client.get_collection(args.collection)
    except Exception as error:
        raise BaselineError(f"无法读取 Qdrant collection：{args.collection}") from error
    finally:
        qdrant_client.close()
    collection_points = int(collection_info.points_count or 0)
    if collection_points != args.expected_points:
        raise BaselineError(
            f"collection {args.collection} points={collection_points}，"
            f"期望 {args.expected_points}"
        )
    if not settings.reranker_api_key:
        raise BaselineError("RERANKER_API_KEY 未配置，无法得到完整三层 Funnel 基线")

    service = RetrievalService(
        embedding_client=EmbeddingClient(settings.embedding_endpoint, timeout=args.embedding_timeout),
        qdrant=qdrant,
        reranker_model=settings.reranker_model,
        reranker_api_url=settings.reranker_api_url,
        reranker_api_key=settings.reranker_api_key,
    )

    with evaluation_collection(args.collection), details_path.open(
        "a", encoding="utf-8", newline="\n"
    ) as detail_file:
        for index, question in enumerate(questions, 1):
            if question.question_id in results:
                print(f"[{index}/{len(questions)}] {question.question_id} 已完成，跳过", flush=True)
                continue
            started = time.monotonic()
            error: str | None = None
            documents: list[dict[str, Any]] = []
            try:
                payload = await service.retrieve(question.question)
                documents = [_result_document(item) for item in payload.documents]
            except Exception as exception:  # 逐题落盘，单个远端失败不丢失整个基线
                error = f"{type(exception).__name__}: {exception}"
            latency = time.monotonic() - started
            metrics = score_retrieval(question, documents)
            row = {
                **asdict(question),
                "collection": args.collection,
                "retrieved_documents": documents,
                "metrics": metrics,
                "latency_seconds": round(latency, 6),
                "error": error,
            }
            detail_file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            detail_file.flush()
            results[question.question_id] = row
            status = "ERROR" if error else ("HIT" if metrics["hit_at_3"] else "MISS")
            print(
                f"[{index}/{len(questions)}] {question.question_id} {status} "
                f"rank={metrics['first_relevant_rank']} elapsed={latency:.1f}s",
                flush=True,
            )

    summary = build_summary(questions, results, collection=args.collection)
    summary["runtime"] = {
        "collection_points": collection_points,
        "embedding_endpoint": settings.embedding_endpoint,
        "reranker_model": settings.reranker_model,
        "reranker_api_url": settings.reranker_api_url,
        "reranker_key_configured": True,
    }
    _atomic_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA 固定问题集纯召回基线")
    parser.add_argument("--questions", type=Path, required=True, help="prepared/test.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="结果输出目录")
    parser.add_argument("--collection", default="watsonx_docsqa_colab_v1")
    parser.add_argument("--expected-points", type=int, default=6759)
    parser.add_argument("--limit", type=int, help="只跑前 N 道；用于小规模冒烟验证")
    parser.add_argument("--embedding-timeout", type=float, default=60.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(run_baseline(args))
    except BaselineError as error:
        raise SystemExit(f"[FAIL] {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
