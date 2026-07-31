"""从国家法律法规数据库回填劳动合同 A 级资料的公开 metadata。

本工具只搜索每条 metadata 中已有的法律标题，并读取搜索结果返回的公开字段。
它不下载法律原文、不访问用户合同，也不会将内容写入 Qdrant。官方页面未明确
提供的全国适用性、授权状态和本地 Word 与官方正文一致性会继续保持待人工核验。

运行时通过临时 ``cloakbrowser`` 依赖使用受控浏览器；不需要把该依赖写入
生产 Backend 或 Data Worker 镜像。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

from cloakbrowser import launch

SEARCH_URL = "https://flk.npc.gov.cn/search"
DETAIL_URL = "https://flk.npc.gov.cn/detail"
METADATA_SOURCE = "国家法律法规数据库"
PLACEHOLDER = "PENDING_MANUAL_VERIFICATION"
STATUS_BY_CODE = {
    1: "已废止",
    2: "已修改",
    3: "有效",
    4: "尚未生效",
}
FRONT_MATTER = re.compile(r"\A---json\n.*?\n---", re.DOTALL)
HTML_TAG = re.compile(r"<[^>]+>")

FRONT_MATTER_FIELDS = (
    "format_version",
    "doc_id",
    "title",
    "source_level",
    "target_collection",
    "document_type",
    "issuing_authority",
    "jurisdiction",
    "national_applicability",
    "publication_date",
    "effective_date",
    "effective_date_source",
    "effective_date_source_url",
    "effective_date_verified_at",
    "amendment_or_repeal_status",
    "official_url",
    "official_source_id",
    "official_metadata_source",
    "official_metadata_checked_at",
    "official_status_code",
    "content_match_status",
    "review_status",
    "source_filename_date",
    "raw_file",
    "raw_sha256",
)


class MetadataVerificationError(RuntimeError):
    """官方搜索没有返回可安全匹配的资料时抛出。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise MetadataVerificationError(f"metadata 不存在：{path}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise MetadataVerificationError(
                f"metadata 第 {line_number} 行不是有效 JSON"
            ) from error
        doc_id = record.get("doc_id")
        title = record.get("title")
        if not isinstance(doc_id, str) or not doc_id:
            raise MetadataVerificationError(f"metadata 第 {line_number} 行缺少 doc_id")
        if not isinstance(title, str) or not title:
            raise MetadataVerificationError(f"{doc_id} 缺少 title")
        if doc_id in seen:
            raise MetadataVerificationError(f"metadata 中存在重复 doc_id：{doc_id}")
        seen.add(doc_id)
        records.append(record)
    if not records:
        raise MetadataVerificationError("metadata 没有可核验的记录")
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _clean_title(value: str) -> str:
    return HTML_TAG.sub("", value).strip()


def _detail_url(*, source_id: str, title: str) -> str:
    return (
        f"{DETAIL_URL}?id={quote(source_id, safe='')}&fileId=&type="
        f"&title={quote(title, safe='')}"
    )


def _choose_match(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [row for row in rows if _clean_title(str(row.get("title", ""))) == title]
    if not exact:
        candidates = [
            {
                "title": _clean_title(str(row.get("title", ""))),
                "status": STATUS_BY_CODE.get(row.get("sxx"), row.get("sxx")),
                "publication_date": row.get("gbrq"),
            }
            for row in rows[:8]
        ]
        raise MetadataVerificationError(
            f"官方搜索未找到标题完全匹配的资料：{title}；候选：{candidates}"
        )
    # 标题相同但存在历史版本时，只选择数据库标记为“有效”的当前版本。
    effective = [row for row in exact if row.get("sxx") == 3]
    if len(effective) == 1:
        return effective[0]
    if len(effective) > 1:
        raise MetadataVerificationError(f"官方搜索返回多个同名有效资料，需人工选择：{title}")
    if len(exact) == 1:
        raise MetadataVerificationError(
            f"官方搜索只找到非有效版本，未自动回填：{title}（{exact[0].get('sxx')}）"
        )
    raise MetadataVerificationError(f"官方搜索返回多个同名但无有效版本的资料：{title}")


class NPCSearchClient:
    """仅通过受控浏览器调用国家法律法规数据库的公开搜索页面。"""

    def __init__(self, *, timeout_ms: int) -> None:
        self.browser = launch(headless=True, humanize=False, proxy="http://127.0.0.1:7897")
        self.page = self.browser.new_page()
        self.timeout_ms = timeout_ms
        self._search_responses: list[Any] = []
        self.page.on("response", self._capture_search_response)

    def __enter__(self) -> Self:
        self.page.goto(SEARCH_URL, timeout=self.timeout_ms)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.browser.close()

    def _capture_search_response(self, response: Any) -> None:
        if response.url.rstrip("/").endswith("/law-search/search/list"):
            self._search_responses.append(response)

    def search_exact_title(self, title: str) -> dict[str, Any]:
        before = len(self._search_responses)
        selector = "input[placeholder=请输入]"
        self.page.fill(selector, title)
        self.page.press(selector, "Enter")
        self.page.wait_for_timeout(1200)
        responses = self._search_responses[before:]
        if not responses:
            raise MetadataVerificationError(f"未收到官方搜索响应：{title}")
        for response in reversed(responses):
            try:
                payload = json.loads(response.body().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if payload.get("code") != 200:
                continue
            rows = payload.get("rows")
            if isinstance(rows, list):
                return _choose_match(title, rows)
        raise MetadataVerificationError(f"官方搜索响应格式不可用：{title}")


def _rewrite_markdown_front_matter(path: Path, metadata: dict[str, Any]) -> str:
    if not path.is_file():
        raise MetadataVerificationError(f"找不到对应 Markdown：{path}")
    original = path.read_text(encoding="utf-8")
    if not FRONT_MATTER.match(original):
        raise MetadataVerificationError(f"Markdown 缺少 JSON front matter：{path}")
    header = {
        field: metadata[field]
        for field in FRONT_MATTER_FIELDS
        if field in metadata
    }
    replacement = "---json\n" + json.dumps(header, ensure_ascii=False, indent=2) + "\n---"
    path.write_text(FRONT_MATTER.sub(replacement, original, count=1), encoding="utf-8")
    return _sha256(path)


def _update_record(record: dict[str, Any], official: dict[str, Any], *, checked_at: str) -> None:
    source_id = official.get("bbbs")
    if not isinstance(source_id, str) or not source_id:
        raise MetadataVerificationError(f"官方搜索结果缺少资料标识：{record['title']}")
    status_code = official.get("sxx")
    status = STATUS_BY_CODE.get(status_code)
    if status != "有效":
        raise MetadataVerificationError(f"官方结果不是有效资料：{record['title']} ({status})")

    record.update(
        {
            "document_type": official.get("flxz") or PLACEHOLDER,
            "issuing_authority": official.get("zdjgName") or PLACEHOLDER,
            "publication_date": official.get("gbrq") or PLACEHOLDER,
            "effective_date": official.get("sxrq") or PLACEHOLDER,
            "amendment_or_repeal_status": status,
            "official_url": _detail_url(source_id=source_id, title=record["title"]),
            "official_source_id": source_id,
            "official_metadata_source": METADATA_SOURCE,
            "official_metadata_checked_at": checked_at,
            "official_status_code": status_code,
            # 必须另行比对官方正文，不能因标题和日期匹配就认定 Word 内容一致。
            "content_match_status": "PENDING_OFFICIAL_TEXT_COMPARISON",
            "review_status": "OFFICIAL_METADATA_FETCHED_PENDING_CONTENT_AND_SCOPE_REVIEW",
        }
    )


def _update_manifest(path: Path, records: list[dict[str, Any]], *, checked_at: str) -> None:
    if not path.is_file():
        raise MetadataVerificationError(f"manifest 不存在：{path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MetadataVerificationError(f"manifest 不是有效 JSON：{path}") from error
    manifest["documents"] = records
    manifest["status"] = "OFFICIAL_METADATA_FETCHED_PENDING_CONTENT_AND_SCOPE_REVIEW"
    manifest["official_metadata_verification"] = {
        "source": METADATA_SOURCE,
        "checked_at": checked_at,
        "documents_checked": len(records),
        "verified_fields": [
            "official_url",
            "document_type",
            "issuing_authority",
            "publication_date",
            "effective_date",
            "amendment_or_repeal_status",
        ],
        "still_pending": [
            "jurisdiction",
            "national_applicability",
            "license_status",
            "official_text_content_match",
        ],
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify(*, base_dir: Path, timeout_seconds: int, dry_run: bool) -> dict[str, Any]:
    metadata_path = base_dir / "metadata" / "a_level_documents.jsonl"
    manifest_path = base_dir / "manifests" / "legal_labor_a_v1_preparation.json"
    records = _read_jsonl(metadata_path)
    checked_at = datetime.now(timezone.utc).isoformat()

    with NPCSearchClient(timeout_ms=timeout_seconds * 1000) as client:
        for record in records:
            official = client.search_exact_title(record["title"])
            _update_record(record, official, checked_at=checked_at)

    if not dry_run:
        for record in records:
            markdown_path = base_dir / record["normalized_markdown"]
            record["normalized_markdown_sha256"] = _rewrite_markdown_front_matter(
                markdown_path,
                record,
            )
        _write_jsonl(metadata_path, records)
        _update_manifest(manifest_path, records, checked_at=checked_at)

    return {
        "status": "verified" if not dry_run else "dry_run_verified",
        "documents": len(records),
        "source": METADATA_SOURCE,
        "checked_at": checked_at,
        "metadata": str(metadata_path),
        "manifest": str(manifest_path),
    }


def parse_args() -> argparse.Namespace:
    workspace_root = Path(__file__).resolve().parents[1]
    default_base = workspace_root / "data" / "legal" / "labor_contract"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=default_base, help="劳动合同资料根目录")
    parser.add_argument("--timeout", type=int, default=30, help="单次页面加载超时秒数")
    parser.add_argument("--dry-run", action="store_true", help="只读取官方 metadata，不写本地文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = verify(
            base_dir=args.base.resolve(),
            timeout_seconds=args.timeout,
            dry_run=args.dry_run,
        )
    except MetadataVerificationError as error:
        print(f"[FAIL] {error}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
