#!/usr/bin/env python3
"""在 watsonxDocsQA 30 道固定问题上生成生产同构 RAG 答案。

该脚本运行在现有 Backend 容器内，按以下路径执行每道题：

``RetrievalService.retrieve`` → 结构化 ``RetrievalPayload`` →
``AgentNodes.generate_answer`` → 生产 ``ANSWER_PROMPT`` → 主 LLM。

评测进程会临时把 Funnel collection 指向独立的 watsonxDocsQA collection；
不会修改生产源码或正在运行的 Backend。生成与 RAGAS 评分刻意拆开：答案一旦
成功便追加写入 ``details.jsonl``，后续评分框架安装或调用失败不会丢失答案。

重跑相同命令会校验问题、Gold、collection 和生成模型后跳过成功题。失败题只
记录在 ``failures.jsonl``，不会标记为完成，因此下次运行会自动重试。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from .watsonx_docsqa_retrieval_baseline import (
        BaselineError,
        Question,
        _atomic_json,
        _load_runtime_settings,
        _result_document,
        evaluation_collection,
        load_questions,
    )
else:  # 直接执行 /app/evaluation/*.py
    from watsonx_docsqa_retrieval_baseline import (  # type: ignore[no-redef]
        BaselineError,
        Question,
        _atomic_json,
        _load_runtime_settings,
        _result_document,
        evaluation_collection,
        load_questions,
    )


FORMAT_VERSION = 1
DETAILS_FILENAME = "details.jsonl"
FAILURES_FILENAME = "failures.jsonl"
SUMMARY_FILENAME = "summary.json"


def load_existing_generations(
    path: Path,
    questions: dict[str, Question],
    *,
    collection: str,
    model: str,
) -> dict[str, dict[str, Any]]:
    """加载成功断点，并拒绝混入不同问题集或运行配置。"""

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
                    f"已有生成结果第 {row_number} 行不是合法 JSON"
                ) from error
            question_id = str(row.get("question_id") or "")
            expected = questions.get(question_id)
            if expected is None:
                raise BaselineError(f"已有生成结果包含未知 ID：{question_id}")
            if row.get("question") != expected.question:
                raise BaselineError(f"已有生成结果题目不一致：{question_id}")
            if row.get("gold_doc_ids") != expected.gold_doc_ids:
                raise BaselineError(f"已有生成结果 Gold 不一致：{question_id}")
            if row.get("collection") != collection:
                raise BaselineError(f"已有生成结果 collection 不一致：{question_id}")
            if row.get("generator", {}).get("model") != model:
                raise BaselineError(f"已有生成结果模型不一致：{question_id}")
            if not str(row.get("answer") or "").strip():
                raise BaselineError(f"已有生成结果答案为空：{question_id}")
            if question_id in existing:
                raise BaselineError(f"已有生成结果重复 question_id：{question_id}")
            existing[question_id] = row
    return existing


async def generate_question(
    question: Question,
    *,
    retrieval_service: Any,
    agent_nodes: Any,
) -> dict[str, Any]:
    """执行一次生产同构的强制检索与答案生成。"""

    from langchain_core.messages import HumanMessage, ToolMessage

    retrieval_started = time.monotonic()
    payload = await retrieval_service.retrieve(question.question)
    retrieval_seconds = time.monotonic() - retrieval_started

    generation_started = time.monotonic()
    state = {
        "messages": [
            HumanMessage(content=question.question),
            ToolMessage(
                content=payload.model_dump_json(),
                tool_call_id=f"watsonx_eval_{question.question_id}",
            ),
        ],
        "summary": "",
    }
    generated = await agent_nodes.generate_answer(state)
    messages = generated.get("messages") or []
    answer = str(getattr(messages[-1], "content", "") or "").strip() if messages else ""
    generation_seconds = time.monotonic() - generation_started
    if not answer:
        raise BaselineError(f"{question.question_id} 的生成答案为空")

    return {
        **asdict(question),
        "answer": answer,
        "contexts": list(payload.contexts),
        "documents": [_result_document(item) for item in payload.documents],
        "latency_seconds": {
            "retrieval": round(retrieval_seconds, 6),
            "generation": round(generation_seconds, 6),
            "total": round(retrieval_seconds + generation_seconds, 6),
        },
    }


def build_generation_summary(
    questions: list[Question],
    results: dict[str, dict[str, Any]],
    *,
    collection: str,
    model: str,
    base_url: str,
    collection_points: int,
    failed_question_ids: list[str],
) -> dict[str, Any]:
    """汇总生成覆盖率、上下文数量和分阶段延迟，不混入 RAGAS 分数。"""

    ordered = [results[item.question_id] for item in questions if item.question_id in results]
    retrieval_latencies = [
        float(row.get("latency_seconds", {}).get("retrieval") or 0.0) for row in ordered
    ]
    generation_latencies = [
        float(row.get("latency_seconds", {}).get("generation") or 0.0) for row in ordered
    ]
    total_latencies = [
        float(row.get("latency_seconds", {}).get("total") or 0.0) for row in ordered
    ]
    context_counts = [len(row.get("contexts") or []) for row in ordered]

    def mean(values: list[float]) -> float:
        return round(statistics.fmean(values), 6) if values else 0.0

    return {
        "format_version": FORMAT_VERSION,
        "dataset": "watsonxDocsQA",
        "split": "test",
        "generated_at": datetime.now(UTC).isoformat(),
        "total_questions": len(questions),
        "completed_questions": len(ordered),
        "failed_questions": len(failed_question_ids),
        "failed_question_ids": failed_question_ids,
        "collection": collection,
        "collection_points": collection_points,
        "generator": {
            "contract": "RetrievalService + AgentNodes.generate_answer + ANSWER_PROMPT",
            "model": model,
            "base_url": base_url,
            "temperature": 0.1,
        },
        "mean_contexts_per_answer": mean([float(value) for value in context_counts]),
        "mean_answer_characters": mean(
            [float(len(str(row.get("answer") or ""))) for row in ordered]
        ),
        "mean_latency_seconds": {
            "retrieval": mean(retrieval_latencies),
            "generation": mean(generation_latencies),
            "total": mean(total_latencies),
        },
    }


async def run_generation_baseline(args: argparse.Namespace) -> dict[str, Any]:
    """顺序执行固定题集，成功后逐题持久化。"""

    questions = load_questions(args.questions.resolve(), args.limit)
    lookup = {item.question_id: item for item in questions}
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    details_path = output_dir / DETAILS_FILENAME
    failures_path = output_dir / FAILURES_FILENAME
    summary_path = output_dir / SUMMARY_FILENAME

    settings = _load_runtime_settings()
    model = settings.main_model.replace("[1m]", "").strip()
    if not settings.openai_api_key:
        raise BaselineError("主 LLM API Key 未配置")

    existing = load_existing_generations(
        details_path,
        lookup,
        collection=args.collection,
        model=model,
    )

    from app.agent.nodes import AgentNodes
    from app.infrastructure.embedding_client import EmbeddingClient
    from app.infrastructure.model_provider import ModelProvider
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
        raise BaselineError("RERANKER_API_KEY 未配置")

    retrieval_service = RetrievalService(
        embedding_client=EmbeddingClient(
            settings.embedding_endpoint,
            timeout=args.embedding_timeout,
        ),
        qdrant=qdrant,
        reranker_model=settings.reranker_model,
        reranker_api_url=settings.reranker_api_url,
        reranker_api_key=settings.reranker_api_key,
    )
    llm = ModelProvider(
        model=model,
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    ).create_chat_model()
    nodes = AgentNodes(llm=llm, llm_with_tools=llm)
    failed_question_ids: list[str] = []

    with evaluation_collection(args.collection), details_path.open(
        "a", encoding="utf-8", newline="\n"
    ) as detail_file, failures_path.open("a", encoding="utf-8", newline="\n") as failure_file:
        for index, question in enumerate(questions, 1):
            if question.question_id in existing:
                print(
                    f"[{index}/{len(questions)}] {question.question_id} 已生成，跳过",
                    flush=True,
                )
                continue
            started = time.monotonic()
            try:
                row = await asyncio.wait_for(
                    generate_question(
                        question,
                        retrieval_service=retrieval_service,
                        agent_nodes=nodes,
                    ),
                    timeout=args.question_timeout,
                )
                row.update(
                    {
                        "format_version": FORMAT_VERSION,
                        "dataset": "watsonxDocsQA",
                        "split": "test",
                        "collection": args.collection,
                        "generator": {
                            "model": model,
                            "base_url": settings.openai_base_url,
                            "temperature": 0.1,
                        },
                    }
                )
                detail_file.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                detail_file.flush()
                existing[question.question_id] = row
                print(
                    f"[{index}/{len(questions)}] {question.question_id} OK "
                    f"answer_chars={len(row['answer'])} "
                    f"elapsed={row['latency_seconds']['total']:.1f}s",
                    flush=True,
                )
            except Exception as error:  # 单题失败不丢失已完成答案
                failed_question_ids.append(question.question_id)
                failure = {
                    "question_id": question.question_id,
                    "question": question.question,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                    "error": f"{type(error).__name__}: {error}",
                }
                failure_file.write(
                    json.dumps(failure, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                failure_file.flush()
                print(
                    f"[{index}/{len(questions)}] {question.question_id} ERROR "
                    f"{failure['error']}",
                    flush=True,
                )

    summary = build_generation_summary(
        questions,
        existing,
        collection=args.collection,
        model=model,
        base_url=settings.openai_base_url,
        collection_points=collection_points,
        failed_question_ids=failed_question_ids,
    )
    _atomic_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA 生产同构答案生成基线")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collection", default="watsonx_docsqa_colab_v1")
    parser.add_argument("--expected-points", type=int, default=6759)
    parser.add_argument("--embedding-timeout", type=float, default=60.0)
    parser.add_argument("--question-timeout", type=float, default=180.0)
    parser.add_argument("--limit", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.question_timeout <= 0:
        raise SystemExit("[FAIL] question-timeout 必须大于 0")
    try:
        asyncio.run(run_generation_baseline(args))
    except BaselineError as error:
        raise SystemExit(f"[FAIL] {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
