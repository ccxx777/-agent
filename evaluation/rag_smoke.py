#!/usr/bin/env python3
"""不依赖 RAGAS 的在线 RAG 冒烟检查。

验证 Backend 能返回答案、真实生成上下文和结构化召回文档。该脚本不计算
任何评测分数，适合每次部署或重启后先确认 RAG 主链是否稳定。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx


async def run_smoke(backend_url: str, question: str, timeout: float) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{backend_url.rstrip('/')}/api/eval/rag_query",
            json={"question": question},
        )
        response.raise_for_status()
        result = response.json()

    answer = result.get("answer")
    contexts = result.get("contexts")
    documents = result.get("documents")

    errors = []
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer 为空或类型错误")
    if not isinstance(contexts, list) or not contexts:
        errors.append("contexts 为空或类型错误")
    if not isinstance(documents, list) or not documents:
        errors.append("documents 为空或类型错误")
    elif len(documents) != len(contexts):
        errors.append("documents 与 contexts 数量不一致")

    for expected_rank, document in enumerate(documents or [], 1):
        if document.get("rank") != expected_rank:
            errors.append(f"第 {expected_rank} 条文档 rank 不连续")
        if not document.get("chunk_id"):
            errors.append(f"第 {expected_rank} 条文档缺少 chunk_id")

    return {"ok": not errors, "errors": errors, "result": result}


async def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 主链冒烟检查")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--question",
        default="博士生中期考核不合格后如何处理？",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    try:
        report = await run_smoke(args.backend_url, args.question, args.timeout)
    except Exception as exc:
        print(f"[FAIL] RAG 请求失败: {exc}", file=sys.stderr)
        return 1

    result = report["result"]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"answer_chars={len(result.get('answer', ''))}")
        print(f"contexts={len(result.get('contexts') or [])}")
        print(f"documents={len(result.get('documents') or [])}")

    if not report["ok"]:
        for error in report["errors"]:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1

    print("[OK] RAG 主链返回结构稳定")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

