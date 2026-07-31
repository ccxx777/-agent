#!/usr/bin/env python3
"""对独立 A 级法律 Collection 执行检索 Smoke Test。

该脚本只调用真实 Embedding、Cascade Funnel 和 Qdrant，不运行合同 Workflow，
也不会修改任何 Collection。它重点验证：结果来自 A 级资料、可引用、能回溯
官方链接，并且 pending 法律资料只能通过显式 staging 开关读取。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LegalRetrievalSmokeError(RuntimeError):
    """Smoke Test 输入或运行配置不满足要求。"""


def discover_backend_import_root(repo_root: Path) -> Path:
    candidates = (repo_root / "backend", repo_root)
    for candidate in candidates:
        if (candidate / "app").is_dir():
            return candidate
    raise LegalRetrievalSmokeError("无法定位 backend/app Python 包")


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_IMPORT_ROOT = discover_backend_import_root(REPO_ROOT)
if str(BACKEND_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_IMPORT_ROOT))

from app.components.retriever.qdrant.v2_0_0.main import (
    DEFAULT_QDRANT_URL,
)
from app.infrastructure.embedding_client import EmbeddingClient
from app.infrastructure.qdrant import QdrantGateway
from app.services.legal_retrieval_service import (
    LegalRetrievalService,
)
from app.services.retrieval_service import RetrievalService

DEFAULT_QUERIES = (
    "劳动合同应当具备哪些条款",
    "试用期最长可以约定多久",
    "劳动合同应当在什么时候订立",
    "用人单位是否应当为劳动者缴纳社会保险",
    "解除劳动合同的经济补偿如何计算",
)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A 级劳动合同法律库检索 Smoke Test")
    parser.add_argument("--collection", default=_env("LEGAL_A_COLLECTION", "legal_labor_a_v1"))
    parser.add_argument("--qdrant-url", default=_env("QDRANT_URL", DEFAULT_QDRANT_URL))
    parser.add_argument(
        "--embed-url",
        default=_env("EMBED_URL", "http://embedding_service:8001/embed"),
    )
    parser.add_argument(
        "--reranker-model",
        default=_env("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
    )
    parser.add_argument(
        "--reranker-api-url",
        default=_env("RERANKER_API_URL", "https://api.siliconflow.cn/v1/rerank"),
    )
    parser.add_argument(
        "--reranker-api-key",
        default=_env("RERANKER_API_KEY", _env("ANTHROPIC_AUTH_TOKEN", "")),
    )
    parser.add_argument(
        "--allow-pending-governance",
        action="store_true",
        help="仅用于尚未完成法律复核的 staging Collection",
    )
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="覆盖默认问题；可重复传入",
    )
    parser.add_argument("--output", type=Path, help="可选 JSON 输出路径")
    return parser


def _document_summary(document: Any) -> dict[str, Any]:
    metadata = document.metadata
    return {
        "rank": document.rank,
        "doc_id": document.doc_id,
        "chunk_id": document.chunk_id,
        "title": document.title,
        "article_no": metadata.get("article_no", ""),
        "citation_label": metadata.get("citation_label", ""),
        "citation_eligible": metadata.get("citation_eligible"),
        "effective_date": metadata.get("effective_date", ""),
        "official_url": metadata.get("official_url", document.source),
        "legal_activation_status": metadata.get("legal_activation_status", ""),
    }


def _validate_documents(documents: list[Any]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        metadata = document.metadata
        if metadata.get("source_level") != "A":
            errors.append(f"{document.doc_id}: source_level 不是 A")
        if metadata.get("citation_eligible") is not True:
            errors.append(f"{document.doc_id}: citation_eligible 不是 true")
        if not metadata.get("article_no"):
            errors.append(f"{document.doc_id}: 缺少 article_no")
        if not (metadata.get("official_url") or document.source):
            errors.append(f"{document.doc_id}: 缺少 official_url")
    return errors


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    if not args.collection.startswith("legal_"):
        raise LegalRetrievalSmokeError("Smoke Test 只允许查询 legal_ 前缀 Collection")
    queries = tuple(args.queries or DEFAULT_QUERIES)
    if not queries:
        raise LegalRetrievalSmokeError("至少需要一个测试问题")

    embedding_client = EmbeddingClient(args.embed_url, timeout=60.0)
    retrieval = RetrievalService(
        embedding_client=embedding_client,
        qdrant=QdrantGateway(args.qdrant_url),
        reranker_model=args.reranker_model,
        reranker_api_url=args.reranker_api_url,
        reranker_api_key=args.reranker_api_key,
        collection_name=args.collection,
    )
    legal_retrieval = LegalRetrievalService(
        retrieval_service=retrieval,
        collection_name=args.collection,
        source_level="A",
        allow_pending_governance=args.allow_pending_governance,
    )

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, query in enumerate(queries, 1):
        question_started = time.monotonic()
        try:
            payload = await legal_retrieval.retrieve(query)
            validation_errors = _validate_documents(payload.documents)
            if not payload.documents:
                validation_errors.append("没有返回可引用 A 级法条")
            rows.append(
                {
                    "query": query,
                    "status": "passed" if not validation_errors else "failed",
                    "latency_seconds": round(time.monotonic() - question_started, 3),
                    "documents": [_document_summary(document) for document in payload.documents],
                    "errors": validation_errors,
                }
            )
            print(
                f"[{index}/{len(queries)}] {query} -> "
                f"{len(payload.documents)} docs "
                f"({'OK' if not validation_errors else 'FAIL'})",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - smoke test reports per-query failure
            rows.append(
                {
                    "query": query,
                    "status": "failed",
                    "latency_seconds": round(time.monotonic() - question_started, 3),
                    "documents": [],
                    "errors": [str(error)],
                }
            )
            print(f"[{index}/{len(queries)}] {query} -> ERROR: {error}", flush=True)

    failed = [row for row in rows if row["status"] != "passed"]
    return {
        "format_version": 1,
        "status": "passed" if not failed else "failed",
        "collection": args.collection,
        "source_level": "A",
        "allow_pending_governance": args.allow_pending_governance,
        "queries": len(rows),
        "failed_queries": len(failed),
        "generated_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": rows,
    }


async def main_async(args: argparse.Namespace) -> int:
    result = await run_smoke(args)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result["status"] == "passed" else 1


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return asyncio.run(main_async(args))
    except (LegalRetrievalSmokeError, OSError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
