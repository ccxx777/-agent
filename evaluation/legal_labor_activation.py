#!/usr/bin/env python3
"""Safely activate the staged A-level labor-law corpus.

Activation is deliberately separate from ingestion.  The prepared artifact and
Qdrant payload both carry ``legal_activation_status``; changing only one of them
would either make a future re-ingest unsafe or make the Backend filter every
legal source out.  ``preflight`` is read-only.  ``activate`` requires explicit
reviewer confirmations, creates a local backup, updates the artifact, updates
Qdrant, and verifies that every point is active.

This script handles public legal corpus metadata only.  It never reads user
contracts and never writes to ``rag_chunks`` or a watsonx evaluation collection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# Keep the documented ``python evaluation/...py`` invocation working.  Python
# otherwise puts only the evaluation directory on sys.path and cannot import
# the sibling ``data_worker`` package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_worker.ingest.legal_corpus import (
    LegalCorpusError,
    validate_prepared_corpus,
)

DEFAULT_COLLECTION = "legal_labor_a_v1"
DEFAULT_BASE = Path("data/legal/labor_contract")
PENDING_STATUS = "PENDING_LEGAL_REVIEW"
ACTIVE_STATUS = "ACTIVE"
OFFICIAL_SOURCE = "国家法律法规数据库"
OFFICIAL_URL_PREFIX = "https://flk.npc.gov.cn/"
OFFICIAL_PUBLIC_SOURCE = "OFFICIAL_PUBLIC_SOURCE"
CONTENT_MATCH_STATUS = "VERIFIED_OFFICIAL_TEXT_MATCH"
REVIEW_STATUS = "LEGAL_REVIEW_CONFIRMED"
JURISDICTION = "中国大陆"
FRONT_MATTER = re.compile(r"\A---json\n(?P<payload>.*?)\n---", re.DOTALL)
PENDING_NOTE = re.compile(
    r"> 本文件由本地 DOCX 自动转换。法律效力、制定机关、官方链接和施行日期尚未人工核验；\s*"
    r"> 不应据此直接作出正式法律结论或导入生产资料库。",
    re.DOTALL,
)
CONFIRMED_NOTE = (
    "> 本文件由本地 DOCX 自动转换，元数据和正文已按国家法律法规数据库官方条目核验；"
    "本项目仍只提供参考性信息，不替代法律专业意见。"
)


class ActivationError(RuntimeError):
    """法律资料激活前置条件或一致性检查失败。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ActivationError(f"文件不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise ActivationError(f"JSON 无效：{path}") from error
    if not isinstance(value, dict):
        raise ActivationError(f"JSON 顶层不是对象：{path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise ActivationError(f"文件不存在：{path}") from error
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ActivationError(f"JSONL 第 {line_number} 行无效：{path}") from error
        if not isinstance(value, dict):
            raise ActivationError(f"JSONL 第 {line_number} 行不是对象：{path}")
        records.append(value)
    if not records:
        raise ActivationError(f"JSONL 没有记录：{path}")
    return records


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    _atomic_text(path, content)


def _qdrant_result(response: httpx.Response) -> Any:
    if response.is_error:
        raise ActivationError(
            f"Qdrant HTTP {response.status_code}：{response.text.strip()[:500]}"
        )
    try:
        payload = response.json()
    except ValueError as error:
        raise ActivationError("Qdrant 返回了无效 JSON") from error
    if payload.get("status") not in (None, "ok"):
        raise ActivationError(f"Qdrant 返回非 ok 状态：{payload.get('status')}")
    return payload.get("result")


def _qdrant_version(client: httpx.Client, qdrant_url: str) -> str:
    response = client.get(qdrant_url.rstrip("/") + "/")
    response.raise_for_status()
    version = str(response.json().get("version") or "")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(int(part) for part in match.groups()) < (1, 10, 0):
        raise ActivationError(f"Qdrant 版本不满足激活要求：{version!r}，至少需要 1.10.0")
    return version


def _qdrant_point_count(
    client: httpx.Client, *, qdrant_url: str, collection: str
) -> int:
    result = _qdrant_result(
        client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/count",
            json={"exact": True},
        )
    )
    if not isinstance(result, dict) or "count" not in result:
        raise ActivationError("无法读取 Qdrant 精确 Point 数")
    return int(result["count"])


def _qdrant_snapshot(
    client: httpx.Client, *, qdrant_url: str, collection: str
) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 1_000,
            "with_payload": ["legal_activation_status"],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        result = _qdrant_result(
            client.post(
                f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
                json=body,
            )
        )
        if not isinstance(result, dict):
            raise ActivationError("Qdrant scroll 返回格式无效")
        points = result.get("points") or []
        for point in points:
            if not isinstance(point, dict) or not point.get("id"):
                raise ActivationError("Qdrant scroll 返回了缺少 id 的 Point")
            payload = point.get("payload") or {}
            snapshot[str(point["id"])] = str(payload.get("legal_activation_status") or "")
        offset = result.get("next_page_offset")
        if not points or offset is None:
            return snapshot


def _update_qdrant_activation(
    client: httpx.Client,
    *,
    qdrant_url: str,
    collection: str,
    point_ids: list[str],
    status: str,
) -> None:
    if not point_ids:
        raise ActivationError("没有可更新的法律 Point")
    # Qdrant 1.10 distinguishes set/merge (POST) from overwrite (PUT).  The
    # latter would delete every existing citation field when changing only the
    # activation flag.
    _qdrant_result(
        client.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/payload",
            params={"wait": "true"},
            json={
                "payload": {"legal_activation_status": status},
                "points": point_ids,
            },
        )
    )


def _prepared_paths(prepared: Path) -> dict[str, Path]:
    return {
        "manifest": prepared / "manifest.json",
        "validation": prepared / "validation.json",
        "articles": prepared / "articles.jsonl",
        "chunks": prepared / "article_chunks.jsonl",
    }


def _bundle(base: Path, prepared: Path) -> dict[str, Any]:
    paths = _prepared_paths(prepared)
    manifest = _read_json(paths["manifest"])
    try:
        validate_prepared_corpus(prepared)
    except (LegalCorpusError, OSError) as error:
        raise ActivationError(f"prepared artifact 校验失败：{error}") from error
    metadata_path = base / "metadata" / "a_level_documents.jsonl"
    metadata = _read_jsonl(metadata_path)
    articles = _read_jsonl(paths["articles"])
    chunks = _read_jsonl(paths["chunks"])
    return {
        "base": base,
        "prepared": prepared,
        "paths": paths,
        "metadata_path": metadata_path,
        "manifest": manifest,
        "metadata": metadata,
        "articles": articles,
        "chunks": chunks,
    }


def _pending_field_counts(metadata: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in metadata:
        for field in ("jurisdiction", "national_applicability", "license_status"):
            value = record.get(field)
            if value is None or str(value).startswith("PENDING_"):
                counts[field] = counts.get(field, 0) + 1
    return counts


def _static_errors(
    *,
    manifest: dict[str, Any],
    metadata: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    collection: str,
) -> list[str]:
    errors: list[str] = []
    if collection.startswith(("rag_chunks", "watsonx_docsqa")):
        errors.append(f"禁止激活到受保护 Collection：{collection}")
    if manifest.get("target_collection") != collection:
        errors.append("manifest target_collection 与命令 Collection 不一致")
    for index, record in enumerate(metadata, 1):
        label = f"metadata[{index}]"
        if record.get("source_level") != "A":
            errors.append(f"{label}.source_level 不是 A")
        if not str(record.get("official_url") or "").startswith(OFFICIAL_URL_PREFIX):
            errors.append(f"{label}.official_url 不是国家法律法规数据库 URL")
        if record.get("official_status_code") != 3:
            errors.append(f"{label}.official_status_code 不是 3（有效）")
        if record.get("amendment_or_repeal_status") != "有效":
            errors.append(f"{label}.amendment_or_repeal_status 不是有效")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(record.get("effective_date") or "")):
            errors.append(f"{label}.effective_date 无效")
    if not chunks:
        errors.append("article_chunks.jsonl 没有记录")
    point_ids = [str(chunk.get("point_id") or "") for chunk in chunks]
    if any(not point_id for point_id in point_ids):
        errors.append("article_chunks.jsonl 存在空 point_id")
    if len(set(point_ids)) != len(point_ids):
        errors.append("article_chunks.jsonl 存在重复 point_id")
    return errors


def _activation_fields(*, reviewer: str, reviewed_at: str) -> dict[str, Any]:
    return {
        "jurisdiction": JURISDICTION,
        "national_applicability": True,
        "license_status": OFFICIAL_PUBLIC_SOURCE,
        "content_match_status": CONTENT_MATCH_STATUS,
        "review_status": REVIEW_STATUS,
        "reviewed_by": reviewer,
        "reviewed_at": reviewed_at,
        "legal_activation_status": ACTIVE_STATUS,
    }


def _update_markdown(path: Path, fields: dict[str, Any]) -> str:
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ActivationError(f"无法读取 normalized Markdown：{path}") from error
    match = FRONT_MATTER.match(original)
    if not match:
        raise ActivationError(f"Markdown 缺少 JSON front matter：{path}")
    try:
        front_matter = json.loads(match.group("payload"))
    except json.JSONDecodeError as error:
        raise ActivationError(f"Markdown front matter 无效：{path}") from error
    if not isinstance(front_matter, dict):
        raise ActivationError(f"Markdown front matter 不是对象：{path}")
    front_matter.update(fields)
    replacement = "---json\n" + json.dumps(front_matter, ensure_ascii=False, indent=2) + "\n---"
    updated = FRONT_MATTER.sub(replacement, original, count=1)
    updated = PENDING_NOTE.sub(CONFIRMED_NOTE, updated, count=1)
    _atomic_text(path, updated)
    return _sha256(path)


def _backup_files(bundle: dict[str, Any], timestamp: str) -> Path:
    base = bundle["base"]
    prepared = bundle["prepared"]
    backup = prepared / "activation_backups" / timestamp.replace(":", "").replace("+00:00", "Z")
    paths: list[Path] = list(bundle["paths"].values()) + [bundle["metadata_path"]]
    for record in bundle["metadata"]:
        relative = Path(str(record.get("normalized_markdown") or ""))
        candidate = (base / relative).resolve()
        if not candidate.is_relative_to(base.resolve()):
            raise ActivationError(f"normalized_markdown 路径越界：{relative}")
        paths.append(candidate)
    for path in dict.fromkeys(paths):
        if path.is_file():
            target = backup / path.resolve().relative_to(base.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return backup


def _write_activated_artifacts(
    bundle: dict[str, Any], *, reviewer: str, reviewed_at: str, review_note: str
) -> None:
    base = bundle["base"]
    paths = bundle["paths"]
    fields = _activation_fields(reviewer=reviewer, reviewed_at=reviewed_at)
    metadata = [dict(record, **fields) for record in bundle["metadata"]]
    markdown_hashes: dict[str, str] = {}
    for record in metadata:
        relative = Path(str(record.get("normalized_markdown") or ""))
        markdown_path = (base / relative).resolve()
        if not markdown_path.is_relative_to(base.resolve()):
            raise ActivationError(f"normalized_markdown 路径越界：{relative}")
        markdown_hashes[str(relative).replace("\\", "/")] = _update_markdown(
            markdown_path, fields
        )
        record["normalized_markdown_sha256"] = markdown_hashes[
            str(relative).replace("\\", "/")
        ]

    articles = [dict(record, **fields) for record in bundle["articles"]]
    chunks = [dict(record, **fields) for record in bundle["chunks"]]
    for record in articles + chunks:
        relative = str(record.get("normalized_markdown") or "")
        if relative in markdown_hashes:
            record["normalized_markdown_sha256"] = markdown_hashes[relative]

    manifest = dict(bundle["manifest"])
    governance = dict(manifest.get("governance") or {})
    governance.update(
        {
            "legal_activation_status": ACTIVE_STATUS,
            "pending_document_fields": [],
            "blocked_actions": [
                "Do not use citation_eligible=false preamble records as a formal legal basis.",
                "Do not write this corpus into rag_chunks or watsonxDocsQA collections.",
            ],
        }
    )
    activation = {
        "status": ACTIVE_STATUS,
        "activated_at": reviewed_at,
        "reviewer": reviewer,
        "review_note": review_note,
        "source": OFFICIAL_SOURCE,
        "confirmations": {
            "national_applicability": True,
            "effective_status": True,
            "official_text_content_match": True,
        },
    }
    manifest["status"] = ACTIVE_STATUS
    manifest["governance"] = governance
    manifest["activation"] = activation
    manifest["input"] = dict(manifest.get("input") or {})
    manifest["input"]["metadata_sha256"] = _sha256(bundle["metadata_path"])

    _atomic_jsonl(bundle["metadata_path"], metadata)
    manifest["input"]["metadata_sha256"] = _sha256(bundle["metadata_path"])
    _atomic_jsonl(paths["articles"], articles)
    _atomic_jsonl(paths["chunks"], chunks)
    manifest["files"] = dict(manifest.get("files") or {})
    manifest["files"]["articles"] = {
        "path": paths["articles"].name,
        "sha256": _sha256(paths["articles"]),
    }
    manifest["files"]["article_chunks"] = {
        "path": paths["chunks"].name,
        "sha256": _sha256(paths["chunks"]),
    }
    _atomic_json(paths["manifest"], manifest)


def _restore_backup(bundle: dict[str, Any], backup: Path) -> None:
    base = bundle["base"].resolve()
    if not backup.is_dir():
        return
    for source in backup.rglob("*"):
        if not source.is_file():
            continue
        target = (base / source.relative_to(backup)).resolve()
        if not target.is_relative_to(base):
            raise ActivationError(f"备份恢复路径越界：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _qdrant_preflight(
    *,
    qdrant_url: str,
    collection: str,
    expected_points: int,
    expected_point_ids: set[str] | None = None,
    timeout: float,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        version = _qdrant_version(client, qdrant_url)
        point_count = _qdrant_point_count(
            client, qdrant_url=qdrant_url, collection=collection
        )
        snapshot = _qdrant_snapshot(
            client, qdrant_url=qdrant_url, collection=collection
        )
    actual_point_ids = set(snapshot)
    expected_ids = expected_point_ids or set()
    missing_point_ids = sorted(expected_ids - actual_point_ids)
    unexpected_point_ids = sorted(actual_point_ids - expected_ids)
    return {
        "qdrant_version": version,
        "points": point_count,
        "expected_points": expected_points,
        "status_counts": {
            status or "<missing>": list(snapshot.values()).count(status)
            for status in sorted(set(snapshot.values()))
        },
        "point_ids": sorted(snapshot),
        "point_count_matches": point_count == expected_points,
        "point_ids_match": not expected_ids
        or (not missing_point_ids and not unexpected_point_ids),
        "missing_point_ids": missing_point_ids,
        "unexpected_point_ids": unexpected_point_ids,
        "all_active": len(snapshot) == expected_points
        and all(status == ACTIVE_STATUS for status in snapshot.values()),
    }


def preflight(
    *, base: Path, prepared: Path, qdrant_url: str, collection: str, timeout: float
) -> dict[str, Any]:
    bundle = _bundle(base.resolve(), prepared.resolve())
    manifest = bundle["manifest"]
    static_errors = _static_errors(
        manifest=manifest,
        metadata=bundle["metadata"],
        chunks=bundle["chunks"],
        collection=collection,
    )
    qdrant = _qdrant_preflight(
        qdrant_url=qdrant_url,
        collection=collection,
        expected_points=len(bundle["chunks"]),
        expected_point_ids={str(chunk["point_id"]) for chunk in bundle["chunks"]},
        timeout=timeout,
    )
    return {
        "status": "ready"
        if not static_errors
        and qdrant["point_count_matches"]
        and qdrant["point_ids_match"]
        else "failed",
        "collection": collection,
        "manifest_status": manifest.get("status"),
        "legal_activation_status": (manifest.get("governance") or {}).get(
            "legal_activation_status"
        ),
        "documents": len(bundle["metadata"]),
        "article_chunks": len(bundle["chunks"]),
        "pending_field_counts": _pending_field_counts(bundle["metadata"]),
        "static_errors": static_errors,
        "qdrant": qdrant,
        "required_confirmations": [
            "--confirm-national-scope",
            "--confirm-effective-status",
            "--confirm-content-match",
        ],
    }


def activate(
    *,
    base: Path,
    prepared: Path,
    qdrant_url: str,
    collection: str,
    reviewer: str,
    review_note: str,
    confirm_national_scope: bool,
    confirm_effective_status: bool,
    confirm_content_match: bool,
    timeout: float,
    apply: bool,
) -> dict[str, Any]:
    if not reviewer.strip():
        raise ActivationError("--reviewer 不能为空")
    bundle = _bundle(base.resolve(), prepared.resolve())
    manifest = bundle["manifest"]
    static_errors = _static_errors(
        manifest=manifest,
        metadata=bundle["metadata"],
        chunks=bundle["chunks"],
        collection=collection,
    )
    if static_errors:
        raise ActivationError("激活前置检查失败：" + "; ".join(static_errors))
    if not all((confirm_national_scope, confirm_effective_status, confirm_content_match)):
        raise ActivationError(
            "激活必须显式确认全国适用、生效状态和官方正文一致性；请同时传入三个 --confirm 参数"
        )

    qdrant = _qdrant_preflight(
        qdrant_url=qdrant_url,
        collection=collection,
        expected_points=len(bundle["chunks"]),
        expected_point_ids={str(chunk["point_id"]) for chunk in bundle["chunks"]},
        timeout=timeout,
    )
    if not qdrant["point_count_matches"] or not qdrant["point_ids_match"]:
        raise ActivationError(
            "Qdrant Point 与 prepared chunk 不一致："
            f"points={qdrant['points']}，missing={qdrant['missing_point_ids'][:3]}，"
            f"unexpected={qdrant['unexpected_point_ids'][:3]}"
        )
    if not apply:
        return {
            "status": "dry_run_ready",
            "collection": collection,
            "manifest_status": manifest.get("status"),
            "legal_activation_status": (manifest.get("governance") or {}).get(
                "legal_activation_status"
            ),
            "qdrant": qdrant,
            "would_update_points": len(bundle["chunks"]),
            "reviewer": reviewer,
        }

    timestamp = datetime.now(UTC).isoformat()
    backup = _backup_files(bundle, timestamp)
    try:
        _write_activated_artifacts(
            bundle,
            reviewer=reviewer.strip(),
            reviewed_at=timestamp,
            review_note=review_note.strip(),
        )
        try:
            validate_prepared_corpus(bundle["prepared"])
        except (LegalCorpusError, OSError) as error:
            raise ActivationError(f"激活后 prepared artifact 校验失败：{error}") from error

        point_ids = [str(chunk["point_id"]) for chunk in bundle["chunks"]]
        with httpx.Client(timeout=timeout) as client:
            _update_qdrant_activation(
                client,
                qdrant_url=qdrant_url,
                collection=collection,
                point_ids=point_ids,
                status=ACTIVE_STATUS,
            )
            snapshot = _qdrant_snapshot(
                client, qdrant_url=qdrant_url, collection=collection
            )
        if set(snapshot) != set(point_ids) or any(
            snapshot[point_id] != ACTIVE_STATUS for point_id in point_ids
        ):
            raise ActivationError("Qdrant 激活后仍存在非 ACTIVE Point")
    except Exception:
        # Artifact changes are restored automatically.  Qdrant is rolled back
        # best-effort because the update endpoint may have completed before a
        # network error was observed.
        try:
            with httpx.Client(timeout=timeout) as client:
                _update_qdrant_activation(
                    client,
                    qdrant_url=qdrant_url,
                    collection=collection,
                    point_ids=[str(chunk["point_id"]) for chunk in bundle["chunks"]],
                    status=PENDING_STATUS,
                )
        except Exception:  # noqa: BLE001, S110 - preserve original activation error
            pass
        _restore_backup(bundle, backup)
        raise

    return {
        "status": "activated",
        "collection": collection,
        "legal_activation_status": ACTIVE_STATUS,
        "points": len(bundle["chunks"]),
        "reviewer": reviewer.strip(),
        "activated_at": timestamp,
        "backup": str(backup),
        "qdrant_status": ACTIVE_STATUS,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--base", type=Path, default=DEFAULT_BASE)
        subparser.add_argument("--prepared", type=Path)
        subparser.add_argument(
            "--qdrant-url", default=os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
        )
        subparser.add_argument("--collection", default=DEFAULT_COLLECTION)
        subparser.add_argument("--timeout", type=float, default=60.0)

    check = subparsers.add_parser("preflight", help="只读检查 manifest、artifact 和 Qdrant")
    add_common(check)

    apply = subparsers.add_parser("activate", help="激活 A 级法律 Collection")
    add_common(apply)
    apply.add_argument("--reviewer", required=True)
    apply.add_argument("--review-note", default="")
    apply.add_argument("--confirm-national-scope", action="store_true")
    apply.add_argument("--confirm-effective-status", action="store_true")
    apply.add_argument("--confirm-content-match", action="store_true")
    apply.add_argument("--apply", action="store_true", help="真正写入；不传则只做激活前置检查")
    return parser


def main() -> int:
    args = _parser().parse_args()
    base = args.base.resolve()
    prepared = (args.prepared or base / "prepared" / "a_level").resolve()
    try:
        if args.command == "preflight":
            result = preflight(
                base=base,
                prepared=prepared,
                qdrant_url=args.qdrant_url,
                collection=args.collection,
                timeout=args.timeout,
            )
        else:
            result = activate(
                base=base,
                prepared=prepared,
                qdrant_url=args.qdrant_url,
                collection=args.collection,
                reviewer=args.reviewer,
                review_note=args.review_note,
                confirm_national_scope=args.confirm_national_scope,
                confirm_effective_status=args.confirm_effective_status,
                confirm_content_match=args.confirm_content_match,
                timeout=args.timeout,
                apply=args.apply,
            )
    except (ActivationError, LegalCorpusError, OSError, httpx.HTTPError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
