#!/usr/bin/env python3
"""RAGAS 离线评测入口。

通过 HTTP 调用生产 RAG Eval API，再使用独立依赖环境计算 RAGAS 指标。该脚本
不导入 Backend 内部实现，防止评测依赖污染生产镜像。

用法：
    uv run --with-requirements evaluation/requirements.txt python evaluation/ragas_eval.py \
        --data data/raw/hust --output data/eval/ragas_result.csv --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ragas_eval")

DEFAULT_BACKEND_URL = "http://localhost:8000"
DEFAULT_EMBED_URL = "http://localhost:8001/embed"
METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_recall", "context_precision"]

# =============================================================================
# 1. Dataset parsing
# =============================================================================


def parse_markdown_qa(md_text: str) -> dict[str, str] | None:
    text = md_text.strip()
    if not text:
        return None
    parts = text.split("## assistant")
    if len(parts) < 2:
        return None
    user_part, assistant_part = parts[0], parts[1]
    if user_part.strip().lower().startswith("## user"):
        user_part = user_part.strip()[len("## user"):]
    q, gt = user_part.strip(), assistant_part.strip()
    if not q or not gt:
        return None
    return {"question": q, "ground_truth": gt}


def parse_dataset(data_path: str, limit: int = 20) -> list[dict[str, str]]:
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path}")

    raw_items: list[dict[str, str]] = []

    if path.is_dir():
        md_files = sorted(path.rglob("*.md"))
        logger.info("Found %d Markdown files in %s", len(md_files), data_path)
        for fp in md_files:
            parsed = parse_markdown_qa(fp.read_text(encoding="utf-8"))
            if parsed:
                raw_items.append(parsed)
            else:
                logger.warning("Failed to parse %s", fp)
    else:
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    raw_items.append({
                        "question": str(obj.get("user", "")).strip(),
                        "ground_truth": str(obj.get("assistant", "")).strip(),
                    })
        elif suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Unsupported JSON format: expected JSON array")
            for obj in data:
                raw_items.append({
                    "question": str(obj.get("user", "")).strip(),
                    "ground_truth": str(obj.get("assistant", "")).strip(),
                })
        else:
            raise ValueError("Unsupported file format: expected .md directory, .jsonl, or .json")

    parsed = []
    for item in raw_items[:limit]:
        if item.get("question") and item.get("ground_truth"):
            parsed.append(item)
    logger.info("Parsed %d valid samples (limit=%d)", len(parsed), limit)
    return parsed


# =============================================================================
# 2. HTTP client - calls backend /api/eval/rag_query
# =============================================================================


async def rag_query(client: httpx.AsyncClient, backend_url: str, question: str) -> dict[str, Any]:
    resp = await client.post(
        f"{backend_url}/api/eval/rag_query",
        json={"question": question},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# =============================================================================
# 3. RAGAS evaluation - one sample at a time with hard timeout
# =============================================================================


async def run_ragas_eval(
    questions: list[str],
    answers: list[str],
    contexts_list: list[list[str]],
    ground_truths: list[str],
    embed_url: str = DEFAULT_EMBED_URL,
) -> list[dict]:
    from openai import OpenAI
    from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate
    from ragas.embeddings.base import BaseRagasEmbedding
    from ragas.llms import llm_factory
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("MAIN_MODEL", "deepseek-v4-flash").replace("[1m]", "").strip()

    evaluator_llm = llm_factory(
        model,
        client=OpenAI(base_url=base_url, api_key=api_key),
        max_tokens=4096,
    )

    class _HTTPEmbeddings(BaseRagasEmbedding):
        async def aembed_texts(self, texts):
            return await asyncio.to_thread(self._embed, texts)
        async def aembed_text(self, text):
            results = await self.aembed_texts([text])
            return results[0]
        def embed_texts(self, texts):
            return self._embed(texts)
        def embed_text(self, text):
            return self._embed([text])[0]
        def embed_documents(self, texts):
            return self._embed(texts)
        def embed_query(self, text):
            return self._embed([text])[0]
        @staticmethod
        def _embed(texts):
            if not texts:
                return []
            resp = httpx.post(embed_url, json={"texts": texts, "dense": True, "sparse": False}, timeout=120)
            resp.raise_for_status()
            return resp.json().get("dense", [])

    evaluator_embeddings = _HTTPEmbeddings()
    metrics = [faithfulness, answer_relevancy, context_recall, context_precision]

    all_results = []
    for idx in range(len(questions)):
        q = questions[idx][:80]
        logger.info("[Eval %d/%d] Starting: %.80s...", idx + 1, len(questions), q)

        sample_ds = EvaluationDataset(samples=[
            SingleTurnSample(
                user_input=questions[idx],
                response=answers[idx],
                retrieved_contexts=contexts_list[idx],
                reference=ground_truths[idx],
            )
        ])

        row: dict[str, Any] = {
            "question": questions[idx],
            "answer": answers[idx],
            "ground_truth": ground_truths[idx],
            "contexts": contexts_list[idx],
        }

        try:
            result = await asyncio.to_thread(
                evaluate,
                sample_ds,
                metrics=metrics,
                llm=evaluator_llm,
                embeddings=evaluator_embeddings,
                raise_exceptions=False,
                run_config=RunConfig(max_retries=1, timeout=60, log_tenacity=True),
                allow_nest_asyncio=False,
            )
            df = result.to_pandas()
            for col in METRIC_NAMES:
                val = df[col].iloc[0] if col in df.columns else float("nan")
                try:
                    row[col] = float(val)
                except (ValueError, TypeError):
                    row[col] = float("nan")
            logger.info("[Eval %d/%d] Done: %s", idx + 1, len(questions),
                        {k: f"{row[k]:.4f}" if isinstance(row.get(k), float) and row.get(k) == row.get(k) else "NaN"
                         for k in METRIC_NAMES})
        except Exception as e:
            logger.warning("[Eval %d/%d] Error: %s, marking NaN", idx + 1, len(questions), e)
            for col in METRIC_NAMES:
                row[col] = float("nan")

        all_results.append(row)

    return all_results


# =============================================================================
# 4. Main
# =============================================================================


async def main():
    p = argparse.ArgumentParser(description="RAGAS evaluation for HUST student handbook QA")
    p.add_argument("--data", required=True, help="Dataset directory of .md files")
    p.add_argument("--output", default="data/eval/ragas_result.csv", help="CSV output path")
    p.add_argument("--limit", type=int, default=20, help="Evaluate first N samples")
    p.add_argument("--backend-url", default=DEFAULT_BACKEND_URL, help="Backend base URL")
    p.add_argument("--embed-url", default=DEFAULT_EMBED_URL, help="Embedding service URL")
    args = p.parse_args()

    samples = parse_dataset(args.data, limit=args.limit)
    if not samples:
        logger.error("No valid samples found")
        sys.exit(1)

    questions: list[str] = []
    answers: list[str] = []
    contexts_list: list[list[str]] = []
    ground_truths: list[str] = []

    # RAG inference - 3 concurrent with staggered start
    async with httpx.AsyncClient(timeout=300) as client:
        sem = asyncio.Semaphore(3)

        async def _infer_one(i: int, sample: dict) -> tuple:
            q, gt = sample["question"], sample["ground_truth"]
            await asyncio.sleep(i * 0.3)
            async with sem:
                logger.info("[%d/%d] Inferencing: %.60s...", i, len(samples), q)
                res = await rag_query(client, args.backend_url, q)
                logger.info("[%d/%d]   answer=%d chars, contexts=%d",
                            i, len(samples), len(res["answer"]), len(res["contexts"]))
                return (i, q, gt, res["answer"], res["contexts"])

        tasks = [_infer_one(i, sample) for i, sample in enumerate(samples, 1)]
        results = await asyncio.gather(*tasks)

    results.sort(key=lambda r: r[0])
    for _, q, gt, ans, ctx in results:
        questions.append(q)
        ground_truths.append(gt)
        answers.append(ans)
        contexts_list.append(ctx)

    # RAGAS evaluation
    rows = await run_ragas_eval(questions, answers, contexts_list, ground_truths, embed_url=args.embed_url)

    df = pd.DataFrame(rows)
    score_cols = [c for c in METRIC_NAMES if c in df.columns]

    print("\n" + "=" * 70)
    print("RAGAS Overall Average")
    print("=" * 70)
    for col in score_cols:
        vals = df[col].dropna()
        print(f"  {col:<25}: {vals.mean():.4f}  (valid={len(vals)}/{len(df)})")
    print("=" * 70)

    print("\nPer-sample scores:")
    for i, row in df.iterrows():
        print(f"\n[{i+1}] question: {row['question'][:60]}...")
        for col in score_cols:
            val = row[col]
            if isinstance(val, float) and (val != val):
                print(f"    {col:<25}: NaN")
            else:
                print(f"    {col:<25}: {val:.4f}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("Results saved: %s", out_path)


if __name__ == "__main__":
    asyncio.run(main())
