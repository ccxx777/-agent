#!/usr/bin/env python3
"""Restore legal Qdrant payload fields without recomputing vectors.

This is a recovery tool for a payload-only incident.  It reads the already
validated, ACTIVE prepared artifact, uses Qdrant's merge endpoint (POST), and
verifies every point afterwards.  It never changes vectors, collection schema,
or user/evaluation collections.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_worker.ingest.writer import chinese_tokens
from evaluation.legal_labor_activation import (
    ACTIVE_STATUS,
    DEFAULT_BASE,
    DEFAULT_COLLECTION,
    ActivationError,
    _bundle,
    _qdrant_point_count,
    _qdrant_result,
    _qdrant_snapshot,
    _qdrant_version,
)


class PayloadRepairError(RuntimeError):
    """法律 Point payload 恢复失败。"""


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build the stable legal retrieval payload from one prepared chunk."""

    chunk_text = str(record.get("chunk_text") or "")
    if not chunk_text.strip():
        raise PayloadRepairError(f"{record.get('point_id')} 缺少 chunk_text")
    payload = {
        "doc_id": record["doc_id"],
        "chunk_id": record["chunk_id"],
        "chunk_text": chunk_text,
        "title": record["title"],
        "source": record["official_url"],
        "official_url": record["official_url"],
        "source_file": record["raw_file"],
        "sha256": record["raw_sha256"],
        "user_id": "public_legal",
        "source_level": record["source_level"],
        "document_type": record["document_type"],
        "issuing_authority": record["issuing_authority"],
        "jurisdiction": record["jurisdiction"],
        "national_applicability": record["national_applicability"],
        "publication_date": record["publication_date"],
        "effective_date": record["effective_date"],
        "amendment_or_repeal_status": record["amendment_or_repeal_status"],
        "official_source_id": record.get("official_source_id"),
        "article_id": record["article_id"],
        "article_no": record["article_no"],
        "article_label": record["article_label"],
        "article_ordinal": record["article_ordinal"],
        "chapter": record["chapter"],
        "section": record["section"],
        "citation_label": record["citation_label"],
        "citation_eligible": record["citation_eligible"],
        "article_text": record["article_text"],
        "article_text_sha256": record["article_text_sha256"],
        "article_start": record["article_start"],
        "article_end": record["article_end"],
        "excerpt_text": record["excerpt_text"],
        "excerpt_sha256": record["excerpt_sha256"],
        "source_file_origin": record["source_file_origin"],
        "legal_activation_status": record["legal_activation_status"],
        "retrieval_schema_version": 2,
        "fulltext_en": chunk_text,
        "fulltext_zh": chunk_text,
        "fulltext_zh_segmented": " ".join(chinese_tokens(chunk_text)),
    }
    for key in (
        "license_status",
        "content_match_status",
        "review_status",
        "reviewed_by",
        "reviewed_at",
    ):
        if key in record:
            payload[key] = record[key]
    return payload


def _snapshot_backup(
    client: httpx.Client, *, qdrant_url: str, collection: str
) -> dict[str, Any]:
    result = _qdrant_result(
        client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/snapshots",
            timeout=120.0,
        )
    )
    if not isinstance(result, dict):
        raise PayloadRepairError("Qdrant snapshot 返回格式无效")
    return {
        "name": result.get("name"),
        "checksum": result.get("checksum"),
        "size": result.get("size"),
    }


def _merge_payload(
    client: httpx.Client,
    *,
    qdrant_url: str,
    collection: str,
    point_id: str,
    payload: dict[str, Any],
) -> None:
    # POST is Qdrant's set/merge operation.  PUT is overwrite and caused the
    # incident this command repairs.
    _qdrant_result(
        client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/payload",
            params={"wait": "true"},
            json={"payload": payload, "points": [point_id]},
        )
    )


def repair(
    *,
    base: Path,
    prepared: Path,
    qdrant_url: str,
    collection: str,
    timeout: float,
    create_snapshot: bool,
) -> dict[str, Any]:
    bundle = _bundle(base.resolve(), prepared.resolve())
    manifest = bundle["manifest"]
    if (manifest.get("governance") or {}).get("legal_activation_status") != ACTIVE_STATUS:
        raise PayloadRepairError("prepared artifact 尚未 ACTIVE，拒绝恢复生产法律 payload")
    chunks = bundle["chunks"]
    point_ids = [str(chunk["point_id"]) for chunk in chunks]
    if len(set(point_ids)) != len(point_ids):
        raise PayloadRepairError("prepared artifact 存在重复 point_id")

    with httpx.Client(timeout=timeout) as client:
        version = _qdrant_version(client, qdrant_url)
        count = _qdrant_point_count(
            client, qdrant_url=qdrant_url, collection=collection
        )
        before = _qdrant_snapshot(
            client, qdrant_url=qdrant_url, collection=collection
        )
        expected_ids = set(point_ids)
        if count != len(point_ids) or set(before) != expected_ids:
            raise PayloadRepairError(
                "Qdrant Point 与 prepared chunk 不一致："
                f"points={count}，expected={len(point_ids)}，"
                f"missing={sorted(expected_ids - set(before))[:3]}，"
                f"unexpected={sorted(set(before) - expected_ids)[:3]}"
            )
        snapshot = _snapshot_backup(
            client, qdrant_url=qdrant_url, collection=collection
        ) if create_snapshot else None
        for chunk in chunks:
            _merge_payload(
                client,
                qdrant_url=qdrant_url,
                collection=collection,
                point_id=str(chunk["point_id"]),
                payload=_payload(chunk),
            )
        after = _qdrant_snapshot(
            client, qdrant_url=qdrant_url, collection=collection
        )
        if len(after) != len(point_ids) or any(
            status != ACTIVE_STATUS for status in after.values()
        ):
            raise PayloadRepairError("payload 修复后仍存在非 ACTIVE Point")

    required = {
        "source_level",
        "citation_eligible",
        "article_no",
        "official_url",
        "effective_date",
        "legal_activation_status",
        "chunk_text",
        "fulltext_en",
        "fulltext_zh",
    }
    # Snapshot currently carries statuses only; fetch the full payload once
    # for a compact validation pass without printing legal text.
    with httpx.Client(timeout=timeout) as client:
        result = _qdrant_result(
            client.post(
                f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
                json={"limit": 1_000, "with_payload": True, "with_vector": False},
            )
        )
    points = (result or {}).get("points", []) if isinstance(result, dict) else []
    missing_fields = sorted(
        {
            field
            for point in points
            for field in required
            if field not in (point.get("payload") or {})
        }
    )
    if len(points) != len(point_ids) or missing_fields:
        raise PayloadRepairError(
            f"payload 恢复后校验失败：points={len(points)}，missing_fields={missing_fields}"
        )
    return {
        "status": "repaired",
        "collection": collection,
        "qdrant_version": version,
        "points": len(points),
        "snapshot": snapshot,
        "payload_fields_verified": sorted(required),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument(
        "--qdrant-url", default=os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    )
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--skip-snapshot", action="store_true", help="不创建 Qdrant 恢复快照"
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    base = args.base.resolve()
    prepared = (args.prepared or base / "prepared" / "a_level").resolve()
    try:
        result = repair(
            base=base,
            prepared=prepared,
            qdrant_url=args.qdrant_url,
            collection=args.collection,
            timeout=args.timeout,
            create_snapshot=not args.skip_snapshot,
        )
    except (ActivationError, PayloadRepairError, OSError, httpx.HTTPError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
