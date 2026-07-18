#!/usr/bin/env python3
"""使用 RAGAS 0.4.3 collections API 评分 watsonxDocsQA 生成基线。

输入必须是 :mod:`watsonx_docsqa_generation_baseline` 已落盘的
``details.jsonl``。本脚本不再调用 RAG 主链，只执行三项互补评分：

* ``AnswerCorrectness``：生成答案相对参考答案的事实与语义正确性；
* ``Faithfulness``：生成答案中的主张是否受实际检索上下文支持；
* ``ContextRelevance``：实际检索上下文与问题是否相关。

每道题单独保存到 ``scores/<question_id>.json``，每完成一个 metric 都原子更新。
RAGAS 或 Judge API 中途失败时，重跑相同命令只会跳过已有成功分数并重试失败项。
答案生成与评分环境相互隔离，RAGAS 不会影响 Backend Docker 镜像。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


FORMAT_VERSION = 1
SUPPORTED_METRICS = (
    "answer_correctness",
    "faithfulness",
    "context_relevance",
)
SAFE_QUESTION_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class RagasBaselineError(RuntimeError):
    """生成数据、运行签名或评分输出不满足可续跑约束。"""


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_generation_rows(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """读取成功生成结果，并校验 RAGAS 所需四类输入。"""

    if not path.is_file():
        raise RagasBaselineError(f"生成结果不存在：{path}")
    if limit is not None and limit <= 0:
        raise RagasBaselineError("limit 必须大于 0")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for row_number, raw_line in enumerate(source, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RagasBaselineError(
                    f"生成结果第 {row_number} 行不是合法 JSON"
                ) from error
            question_id = str(row.get("question_id") or "").strip()
            if not SAFE_QUESTION_ID.fullmatch(question_id):
                raise RagasBaselineError(f"不安全或为空的 question_id：{question_id!r}")
            if question_id in seen:
                raise RagasBaselineError(f"生成结果重复 question_id：{question_id}")
            seen.add(question_id)
            for field in ("question", "answer", "reference_answer"):
                if not str(row.get(field) or "").strip():
                    raise RagasBaselineError(f"{question_id} 的 {field} 为空")
            contexts = row.get("contexts")
            if not isinstance(contexts, list):
                raise RagasBaselineError(f"{question_id} 的 contexts 不是数组")
            row["contexts"] = [str(item) for item in contexts if str(item).strip()]
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise RagasBaselineError("生成结果中没有可评分记录")
    return rows


def selected_input_sha256(rows: list[dict[str, Any]]) -> str:
    """只对会影响三个 metric 的字段签名，排除延迟等无关数据。"""

    canonical = [
        {
            "question_id": row["question_id"],
            "question": row["question"],
            "answer": row["answer"],
            "reference_answer": row["reference_answer"],
            "contexts": row["contexts"],
        }
        for row in rows
    ]
    body = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def metric_arguments(metric_name: str, row: dict[str, Any]) -> dict[str, Any]:
    """把稳定生成契约映射到 RAGAS collections API 的显式参数。"""

    common = {
        "user_input": str(row["question"]),
    }
    if metric_name == "answer_correctness":
        return {
            **common,
            "response": str(row["answer"]),
            "reference": str(row["reference_answer"]),
        }
    if metric_name == "faithfulness":
        return {
            **common,
            "response": str(row["answer"]),
            "retrieved_contexts": list(row["contexts"]),
        }
    if metric_name == "context_relevance":
        return {
            **common,
            "retrieved_contexts": list(row["contexts"]),
        }
    raise RagasBaselineError(f"不支持的 metric：{metric_name}")


def _metric_result_payload(result: Any) -> dict[str, Any]:
    try:
        value = float(result.value)
    except (TypeError, ValueError, AttributeError) as error:
        raise RagasBaselineError("RAGAS metric 未返回数值 value") from error
    if not math.isfinite(value):
        raise RagasBaselineError("RAGAS metric 返回非有限值")
    payload: dict[str, Any] = {
        "value": value,
        "error": None,
    }
    reason = getattr(result, "reason", None)
    if reason:
        payload["reason"] = str(reason)
    return payload


class _HTTPEmbeddingAdapter:
    """延迟创建真正的 BaseRagasEmbedding 子类，避免普通测试导入 RAGAS。"""

    @staticmethod
    def create(endpoint: str, timeout: float) -> Any:
        from ragas.embeddings.base import BaseRagasEmbedding

        class HTTPEmbeddings(BaseRagasEmbedding):
            def embed_text(self, text: str, **kwargs: Any) -> list[float]:
                return self.embed_texts([text], **kwargs)[0]

            def embed_texts(
                self,
                texts: list[str],
                **_kwargs: Any,
            ) -> list[list[float]]:
                if not texts:
                    return []
                response = httpx.post(
                    endpoint,
                    json={"texts": texts, "dense": True, "sparse": False},
                    timeout=timeout,
                )
                response.raise_for_status()
                vectors = response.json().get("dense") or []
                if len(vectors) != len(texts):
                    raise RagasBaselineError("Embedding 返回数量与请求数量不一致")
                return [[float(value) for value in vector] for vector in vectors]

            async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
                return (await self.aembed_texts([text], **kwargs))[0]

            async def aembed_texts(
                self,
                texts: list[str],
                **_kwargs: Any,
            ) -> list[list[float]]:
                if not texts:
                    return []
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        endpoint,
                        json={"texts": texts, "dense": True, "sparse": False},
                    )
                response.raise_for_status()
                vectors = response.json().get("dense") or []
                if len(vectors) != len(texts):
                    raise RagasBaselineError("Embedding 返回数量与请求数量不一致")
                return [[float(value) for value in vector] for vector in vectors]

        return HTTPEmbeddings()


def create_metrics(
    *,
    model: str,
    base_url: str,
    api_key: str,
    embed_url: str,
    embed_timeout: float,
) -> tuple[dict[str, Any], str]:
    """创建 RAGAS 0.4.3 collections 指标，不使用待移除的 legacy API。"""

    import ragas
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerCorrectness,
        ContextRelevance,
        Faithfulness,
    )

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    evaluator_llm = llm_factory(
        model,
        client=client,
        max_tokens=4096,
    )
    embeddings = _HTTPEmbeddingAdapter.create(embed_url, embed_timeout)
    return (
        {
            "answer_correctness": AnswerCorrectness(
                llm=evaluator_llm,
                embeddings=embeddings,
            ),
            "faithfulness": Faithfulness(llm=evaluator_llm),
            "context_relevance": ContextRelevance(llm=evaluator_llm),
        },
        str(ragas.__version__),
    )


def load_score(path: Path, *, input_sha256: str, question_id: str) -> dict[str, Any]:
    """读取单题断点；答案或上下文变化时拒绝沿用旧分数。"""

    if not path.exists():
        return {
            "format_version": FORMAT_VERSION,
            "question_id": question_id,
            "input_sha256": input_sha256,
            "metrics": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RagasBaselineError(f"无法读取评分断点：{path}") from error
    if payload.get("format_version") != FORMAT_VERSION:
        raise RagasBaselineError(f"评分断点版本不一致：{path}")
    if payload.get("question_id") != question_id:
        raise RagasBaselineError(f"评分断点 question_id 不一致：{path}")
    if payload.get("input_sha256") != input_sha256:
        raise RagasBaselineError(f"评分断点输入已变化：{question_id}")
    if not isinstance(payload.get("metrics"), dict):
        raise RagasBaselineError(f"评分断点 metrics 损坏：{path}")
    return payload


def per_question_sha256(row: dict[str, Any]) -> str:
    return selected_input_sha256([row])


async def score_metric_with_retry(
    metric: Any,
    kwargs: dict[str, Any],
    *,
    attempts: int,
    timeout: float,
) -> dict[str, Any]:
    """在框架内部重试之外增加有界外层重试，并保留最终错误。"""

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await asyncio.wait_for(metric.ascore(**kwargs), timeout=timeout)
            payload = _metric_result_payload(result)
            payload["attempts"] = attempt
            return payload
        except Exception as error:
            last_error = error
            if attempt < attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
    assert last_error is not None
    return {
        "value": None,
        "error": f"{type(last_error).__name__}: {last_error}",
        "attempts": attempts,
    }


def build_ragas_summary(
    rows: list[dict[str, Any]],
    score_payloads: dict[str, dict[str, Any]],
    *,
    metrics: list[str],
    model: str,
    base_url: str,
    ragas_version: str,
) -> dict[str, Any]:
    """对有效值求均值，同时单独报告覆盖率和失败题。"""

    summaries: dict[str, Any] = {}
    for metric_name in metrics:
        values: list[float] = []
        failures: list[str] = []
        for row in rows:
            question_id = row["question_id"]
            result = score_payloads.get(question_id, {}).get("metrics", {}).get(metric_name)
            value = result.get("value") if isinstance(result, dict) else None
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append(float(value))
            else:
                failures.append(question_id)
        summaries[metric_name] = {
            "mean": round(statistics.fmean(values), 6) if values else None,
            "scored": len(values),
            "total": len(rows),
            "coverage": round(len(values) / len(rows), 6),
            "failed_question_ids": failures,
        }
    return {
        "format_version": FORMAT_VERSION,
        "dataset": "watsonxDocsQA",
        "split": "test",
        "generated_at": datetime.now(UTC).isoformat(),
        "samples": len(rows),
        "framework": {
            "name": "ragas",
            "version": ragas_version,
            "api": "metrics.collections",
        },
        "evaluator": {
            "model": model,
            "base_url": base_url,
        },
        "metrics": summaries,
    }


async def run_ragas(args: argparse.Namespace) -> dict[str, Any]:
    """逐题逐指标评分，每次成功后立即原子保存。"""

    load_dotenv(args.env_file.resolve() if args.env_file else None, override=True)
    rows = load_generation_rows(args.generations.resolve(), args.limit)
    selected_metrics = list(dict.fromkeys(args.metrics))
    invalid = [name for name in selected_metrics if name not in SUPPORTED_METRICS]
    if invalid:
        raise RagasBaselineError(f"不支持的 metrics：{', '.join(invalid)}")

    model = (
        args.evaluator_model
        or os.getenv("EVALUATOR_MODEL")
        or os.getenv("MAIN_MODEL", "")
    ).replace("[1m]", "").strip()
    base_url = (
        args.evaluator_base_url
        or os.getenv("EVALUATOR_BASE_URL")
        or os.getenv("OPENAI_BASE_URL", "")
    ).strip()
    api_key = os.getenv(args.api_key_env, "").strip()
    if not model or not base_url or not api_key:
        raise RagasBaselineError("Evaluator model/base_url/API key 配置不完整")

    all_metrics, ragas_version = create_metrics(
        model=model,
        base_url=base_url,
        api_key=api_key,
        embed_url=args.embed_url,
        embed_timeout=args.embed_timeout,
    )
    metrics = {name: all_metrics[name] for name in selected_metrics}
    output_dir = args.output.resolve()
    scores_dir = output_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "format_version": FORMAT_VERSION,
        "dataset": "watsonxDocsQA",
        "input_sha256": selected_input_sha256(rows),
        "samples": len(rows),
        "question_ids": [row["question_id"] for row in rows],
        "metrics": selected_metrics,
        "evaluator_model": model,
        "evaluator_base_url": base_url,
        "embedding_url": args.embed_url,
        "ragas_version": ragas_version,
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RagasBaselineError("已有 RAGAS manifest 无法读取") from error
        if existing_manifest != manifest:
            raise RagasBaselineError("已有 RAGAS 输出目录与本次输入/配置不一致")
    else:
        _atomic_json(manifest_path, manifest)

    score_payloads: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        question_id = row["question_id"]
        score_path = scores_dir / f"{question_id}.json"
        payload = load_score(
            score_path,
            input_sha256=per_question_sha256(row),
            question_id=question_id,
        )
        for metric_name, metric in metrics.items():
            existing = payload["metrics"].get(metric_name)
            if isinstance(existing, dict) and isinstance(existing.get("value"), (int, float)):
                print(
                    f"[{index}/{len(rows)}] {question_id} {metric_name} 已完成，跳过",
                    flush=True,
                )
                continue
            print(
                f"[{index}/{len(rows)}] {question_id} {metric_name} 开始",
                flush=True,
            )
            result = await score_metric_with_retry(
                metric,
                metric_arguments(metric_name, row),
                attempts=args.attempts,
                timeout=args.metric_timeout,
            )
            result["scored_at"] = datetime.now(UTC).isoformat()
            payload["metrics"][metric_name] = result
            _atomic_json(score_path, payload)
            status = f"{result['value']:.4f}" if result["value"] is not None else "ERROR"
            print(
                f"[{index}/{len(rows)}] {question_id} {metric_name} {status}",
                flush=True,
            )
        score_payloads[question_id] = payload

    summary = build_ragas_summary(
        rows,
        score_payloads,
        metrics=selected_metrics,
        model=model,
        base_url=base_url,
        ragas_version=ragas_version,
    )
    _atomic_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA RAGAS 0.4.3 评分")
    parser.add_argument("--generations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--evaluator-model")
    parser.add_argument("--evaluator-base-url")
    parser.add_argument("--api-key-env", default="ANTHROPIC_AUTH_TOKEN")
    parser.add_argument("--embed-url", default="http://127.0.0.1:8001/embed")
    parser.add_argument("--embed-timeout", type=float, default=120.0)
    parser.add_argument("--metric-timeout", type=float, default=180.0)
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=list(SUPPORTED_METRICS),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.attempts <= 0:
        raise SystemExit("[FAIL] attempts 必须大于 0")
    try:
        asyncio.run(run_ragas(args))
    except RagasBaselineError as error:
        raise SystemExit(f"[FAIL] {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
