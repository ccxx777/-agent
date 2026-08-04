#!/usr/bin/env python3
"""PDF/DOC/DOCX 合同上传、迁移 API 和隐私门禁。

脚本面向服务器真实 Backend API，默认对每个文件执行：上传、轮询解析状态、
读取合同历史、读取会话历史/会话合同列表、删除并确认删除后的 404。它只输出
格式、状态、质量统计和脱敏统计，不输出合同页文本、文件路径或隐私哨兵值。

示例：
    python evaluation/contract_upload_api_smoke.py \
      --file pdf=/tmp/contract.pdf \
      --file doc=/tmp/contract.doc \
      --file docx=/tmp/contract.docx \
      --token "$TOKEN" --require-extraction \
      --privacy-sentinel "13912345678" \
      --expect-redaction phone=1 --output data/contract_upload_gate.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


class ContractUploadGateError(RuntimeError):
    """上传或隐私门禁失败。"""


TERMINAL_REVIEW_STATUSES = {"ready", "needs_confirmation", "failed"}
TERMINAL_EXTRACTION_STATUSES = {"ready", "needs_confirmation", "failed"}
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx"}
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
PRIVATE_KEYS = {
    "storage_path",
    "raw_text",
    "original_text",
    "original_content",
    "raw_content",
    "private_path",
    "source_path",
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合同三格式 API 回归与隐私门禁")
    parser.add_argument(
        "--file",
        action="append",
        required=True,
        metavar="[label=]PATH",
        help="重复传入 PDF、DOC、DOCX；可用 pdf=/path/a.pdf 显式标注格式",
    )
    parser.add_argument("--base-url", default=_env("BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=_env("CONTRACT_TEST_TOKEN", _env("TOKEN")))
    parser.add_argument("--session-id", help="可选：让所有上传绑定到同一个已有 session")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--require-extraction", action="store_true")
    parser.add_argument(
        "--allow-external-ocr",
        action="store_true",
        help="允许扫描页通过外部 OCR 处理；仍会记录 external_raw_image_sent=true",
    )
    parser.add_argument(
        "--privacy-sentinel",
        action="append",
        default=[],
        help="必须不出现在任何 API JSON 中的测试敏感值；可重复传入，不会写入结果",
    )
    parser.add_argument(
        "--expect-redaction",
        action="append",
        default=[],
        metavar="CATEGORY=COUNT",
        help="严格检查每个文件的脱敏计数，例如 phone=1",
    )
    parser.add_argument(
        "--keep-reviews",
        action="store_true",
        help="保留 Smoke 产生的任务；默认删除并验证删除后的 404",
    )
    parser.add_argument("--output", type=Path)
    return parser


def _headers(token: str) -> dict[str, str]:
    if not token:
        raise ContractUploadGateError("缺少 Token，请设置 --token、CONTRACT_TEST_TOKEN 或 TOKEN")
    return {"Authorization": f"Bearer {token}"}


def _progress(message: str) -> None:
    """Write human-readable progress to stderr without corrupting JSON stdout."""

    print(message, file=sys.stderr, flush=True)


def _parse_files(raw_files: list[str]) -> list[tuple[str, Path]]:
    cases: list[tuple[str, Path]] = []
    seen_labels: set[str] = set()
    for raw in raw_files:
        label, raw_path = (raw.split("=", 1) if "=" in raw else ("", raw))
        path = Path(raw_path).expanduser().resolve()
        suffix = path.suffix.lower()
        label = (label.strip().lower() or suffix.removeprefix("."))
        if label not in {"pdf", "doc", "docx"} or suffix not in ALLOWED_SUFFIXES:
            raise ContractUploadGateError(f"文件格式必须是 pdf/doc/docx：{path.name}")
        if label != suffix.removeprefix("."):
            raise ContractUploadGateError(f"格式标签与扩展名不一致：{label} != {suffix}")
        if label in seen_labels:
            raise ContractUploadGateError(f"同一格式重复上传：{label}")
        if not path.is_file():
            raise ContractUploadGateError(f"合同文件不存在：{path}")
        seen_labels.add(label)
        cases.append((label, path))
    expected = {"pdf", "doc", "docx"}
    missing = sorted(expected - seen_labels)
    if missing:
        raise ContractUploadGateError(f"缺少格式：{', '.join(missing)}")
    return cases


def _parse_expected_redactions(raw_values: list[str]) -> dict[str, int]:
    expected: dict[str, int] = {}
    for raw in raw_values:
        if "=" not in raw:
            raise ContractUploadGateError(f"--expect-redaction 格式错误：{raw!r}")
        category, count = raw.split("=", 1)
        try:
            parsed = int(count)
        except ValueError as error:
            raise ContractUploadGateError(f"脱敏计数不是整数：{raw!r}") from error
        if parsed < 0:
            raise ContractUploadGateError(f"脱敏计数不能为负数：{raw!r}")
        expected[category.strip()] = parsed
    return expected


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        # 不把响应正文写入日志，避免后端错误正文意外包含合同片段。
        raise ContractUploadGateError(
            f"HTTP {response.status_code} {response.request.method} {response.request.url.path}"
        ) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise ContractUploadGateError("Backend 返回的不是 JSON") from error
    if not isinstance(payload, dict):
        raise ContractUploadGateError("Backend JSON 顶层不是对象")
    return payload


def _find_private_keys(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text in PRIVATE_KEYS:
                found.append(child_path)
            found.extend(_find_private_keys(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_private_keys(item, f"{path}[{index}]"))
    return found


def validate_public_payload(value: Any, *, privacy_sentinels: list[str]) -> list[str]:
    """检查任意 API JSON 的隐私边界，不要求它是合同详情结构。"""

    errors: list[str] = []
    if _find_private_keys(value):
        errors.append("public response contains private fields")
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for index, sentinel in enumerate(privacy_sentinels):
        if sentinel and sentinel in serialized:
            errors.append(f"public response contains privacy sentinel #{index + 1}")
    return errors


def validate_public_detail(
    detail: dict[str, Any],
    *,
    privacy_sentinels: list[str],
    expected_redactions: dict[str, int],
    require_extraction: bool,
    allow_external_ocr: bool = False,
) -> list[str]:
    """验证 API 公共响应不越过私有文件和脱敏边界。"""

    errors: list[str] = []
    private_keys = _find_private_keys(detail)
    if private_keys:
        errors.append("公共响应包含私有字段")

    serialized = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    for index, sentinel in enumerate(privacy_sentinels):
        if sentinel and sentinel in serialized:
            errors.append(f"公共响应包含第 {index + 1} 个隐私哨兵")

    if not isinstance(detail.get("session_id"), str) or not detail["session_id"]:
        errors.append("响应缺少 session_id，无法证明 005/007 会话绑定")
    if detail.get("retention_policy") not in {"short", "long_opt_in"}:
        errors.append("retention_policy 无效，无法证明 006 留存字段")
    if not isinstance(detail.get("expires_at"), str) or not detail["expires_at"]:
        errors.append("响应缺少 expires_at，无法证明 006 到期字段")

    privacy = detail.get("privacy")
    if not isinstance(privacy, dict):
        errors.append("响应缺少 privacy 对象")
    else:
        external_raw_image_sent = privacy.get("external_raw_image_sent")
        if not isinstance(external_raw_image_sent, bool):
            errors.append("external_raw_image_sent 必须是布尔值")
        elif external_raw_image_sent and not allow_external_ocr:
            errors.append("external_raw_image_sent 必须为 false")
        counts = privacy.get("redaction_counts")
        if not isinstance(counts, dict):
            errors.append("redaction_counts 不是对象")
        else:
            for category, expected in expected_redactions.items():
                if counts.get(category) != expected:
                    errors.append(f"{category} 脱敏计数不符合预期")

    status = detail.get("status")
    if status not in TERMINAL_REVIEW_STATUSES:
        errors.append(f"任务尚未进入终态：{status!r}")
    elif status == "failed":
        errors.append("文件解析状态为 failed")

    if require_extraction and detail.get("extraction_status") not in TERMINAL_EXTRACTION_STATUSES:
        errors.append(f"事实提取尚未进入终态：{detail.get('extraction_status')!r}")
    if require_extraction and detail.get("extraction_status") == "failed":
        errors.append("事实提取状态为 failed")
    return errors


def review_is_terminal(detail: dict[str, Any], *, require_extraction: bool) -> bool:
    """判断上传回归是否可以结束轮询。

    文件解析和事实提取是两个独立状态机。启用 ``--require-extraction`` 时，
    不能在文件状态先进入终态后就提前结束，否则会把合法的 running 状态误判为失败。
    文件解析失败时可以立即结束，因为事实提取不会再产生可用结果。
    """

    review_status = detail.get("status")
    if review_status not in TERMINAL_REVIEW_STATUSES:
        return False
    if review_status == "failed" or not require_extraction:
        return True
    return detail.get("extraction_status") in TERMINAL_EXTRACTION_STATUSES


def _safe_file_result(label: str, detail: dict[str, Any], elapsed: float) -> dict[str, Any]:
    quality = detail.get("quality") or {}
    privacy = detail.get("privacy") or {}
    counts = privacy.get("redaction_counts") if isinstance(privacy, dict) else {}
    return {
        "format": label,
        "review_id": detail.get("review_id"),
        "session_id": detail.get("session_id"),
        "status": detail.get("status"),
        "extraction_status": detail.get("extraction_status"),
        "retention_policy": detail.get("retention_policy"),
        "expires_at_present": bool(detail.get("expires_at")),
        "page_count": quality.get("page_count"),
        "text_coverage": quality.get("text_coverage"),
        "privacy": {
            "redaction_counts": counts if isinstance(counts, dict) else {},
            "zero_width_sequences_detected": privacy.get("zero_width_sequences_detected", 0)
            if isinstance(privacy, dict)
            else 0,
            "external_raw_image_sent": privacy.get("external_raw_image_sent", False)
            if isinstance(privacy, dict)
            else None,
        },
        "elapsed_seconds": round(elapsed, 3),
    }


def _history_check(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    *,
    privacy_sentinels: list[str],
) -> dict[str, Any]:
    history = _json_response(client.get(f"{base_url}/api/contract-reviews/history", headers=headers))
    privacy_errors = validate_public_payload(history, privacy_sentinels=privacy_sentinels)
    if privacy_errors:
        raise ContractUploadGateError("contract history privacy boundary failed")
    reviews = history.get("reviews")
    if not isinstance(reviews, list):
        raise ContractUploadGateError("合同历史响应缺少 reviews 数组")
    return {"status": "passed", "count": len(reviews)}


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    cases = _parse_files(args.file)
    expected_redactions = _parse_expected_redactions(args.expect_redaction)
    headers = _headers(args.token)
    base_url = args.base_url.rstrip("/")
    started = time.monotonic()
    file_results: list[dict[str, Any]] = []
    errors: list[str] = []
    created_ids: list[tuple[str, str]] = []

    with httpx.Client(timeout=60.0) as client:
        _progress("[gate] checking contract history")
        try:
            history_before = _history_check(
                client,
                base_url,
                headers,
                privacy_sentinels=args.privacy_sentinel,
            )
        except ContractUploadGateError as error:
            history_before = {"status": "failed"}
            errors.append(f"迁移 API 回归：合同历史不可用（{error}）")

        for label, path in cases:
            case_started = time.monotonic()
            review_id: str | None = None
            _progress(f"[{label}] uploading {path.name}")
            try:
                data = {"retention_policy": "short"}
                if args.session_id:
                    data["session_id"] = args.session_id
                with path.open("rb") as handle:
                    response = client.post(
                        f"{base_url}/api/contract-reviews",
                        headers=headers,
                        data=data,
                        files={"file": (path.name, handle, MIME_TYPES[path.suffix.lower()])},
                    )
                created = _json_response(response)
                review_id = created.get("review_id")
                _progress(f"[{label}] review_id={review_id}; waiting for extraction")
                if not isinstance(review_id, str) or not review_id:
                    raise ContractUploadGateError("上传响应缺少 review_id")
                created_errors = validate_public_detail(
                    {**created, "status": created.get("status", "queued")},
                    privacy_sentinels=args.privacy_sentinel,
                    expected_redactions={},
                    require_extraction=False,
                    allow_external_ocr=args.allow_external_ocr,
                )
                # 上传瞬间通常还没有 privacy/终态字段，只检查私有字段和哨兵。
                created_errors = [
                    item
                    for item in created_errors
                    if "任务尚未进入终态" not in item
                    and "响应缺少 session_id" not in item
                    and "retention_policy" not in item
                    and "expires_at" not in item
                    and "响应缺少 privacy" not in item
                ]
                if created_errors:
                    raise ContractUploadGateError("上传响应越过隐私边界")

                detail: dict[str, Any] = {}
                poll_count = 0
                deadline = time.monotonic() + max(1.0, args.timeout_seconds)
                while time.monotonic() < deadline:
                    poll_count += 1
                    detail = _json_response(
                        client.get(
                            f"{base_url}/api/contract-reviews/{review_id}", headers=headers
                        )
                    )
                    poll_errors = validate_public_detail(
                        detail,
                        privacy_sentinels=args.privacy_sentinel,
                        expected_redactions={},
                        require_extraction=False,
                        allow_external_ocr=args.allow_external_ocr,
                    )
                    boundary_errors = [
                        item
                        for item in poll_errors
                        if item in {"公共响应包含私有字段"}
                        or item.startswith("公共响应包含第")
                    ]
                    if boundary_errors:
                        raise ContractUploadGateError("任务查询响应越过隐私边界")
                    if review_is_terminal(detail, require_extraction=args.require_extraction):
                        _progress(
                            f"[{label}] terminal status={detail.get('status')} "
                            f"extraction={detail.get('extraction_status')} polls={poll_count}"
                        )
                        break
                    if poll_count == 1 or poll_count % 5 == 0:
                        _progress(
                            f"[{label}] poll={poll_count} status={detail.get('status')} "
                            f"extraction={detail.get('extraction_status')}"
                        )
                    time.sleep(max(0.1, args.poll_seconds))
                else:
                    raise ContractUploadGateError("等待文件解析超时")

                final_errors = validate_public_detail(
                    detail,
                    privacy_sentinels=args.privacy_sentinel,
                    expected_redactions=expected_redactions,
                    require_extraction=args.require_extraction,
                    allow_external_ocr=args.allow_external_ocr,
                )
                if final_errors:
                    raise ContractUploadGateError("；".join(final_errors))
                session_id = detail.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise ContractUploadGateError("合同任务没有可用 session_id")
                created_ids.append((review_id, session_id))

                # 005/007 的真实 API 回归：会话历史与会话绑定合同列表都必须可读。
                chat_history = _json_response(
                    client.get(f"{base_url}/api/chat/history/{session_id}", headers=headers)
                )
                if validate_public_payload(
                    chat_history,
                    privacy_sentinels=args.privacy_sentinel,
                ):
                    raise ContractUploadGateError("chat history privacy boundary failed")
                session_reviews = _json_response(
                    client.get(f"{base_url}/api/sessions/{session_id}/reviews", headers=headers)
                )
                if validate_public_payload(
                    session_reviews,
                    privacy_sentinels=args.privacy_sentinel,
                ):
                    raise ContractUploadGateError("session reviews privacy boundary failed")
                if not isinstance(session_reviews.get("reviews"), list):
                    raise ContractUploadGateError("会话合同列表缺少 reviews 数组")
                file_results.append(
                    _safe_file_result(label, detail, time.monotonic() - case_started)
                    | {"poll_count": poll_count, "deleted": False}
                )
                _progress(f"[{label}] API regression checks passed")
            except (ContractUploadGateError, OSError, httpx.HTTPError) as error:
                errors.append(f"{label} 回归失败：{error}")
                _progress(f"[{label}] failed: {error}")
                file_results.append(
                    {"format": label, "status": "failed", "deleted": False}
                )
            finally:
                if review_id and not args.keep_reviews:
                    try:
                        delete_response = client.delete(
                            f"{base_url}/api/contract-reviews/{review_id}", headers=headers
                        )
                        if delete_response.status_code not in {204, 404}:
                            raise ContractUploadGateError(
                                f"删除返回 HTTP {delete_response.status_code}"
                            )
                        verify = client.get(
                            f"{base_url}/api/contract-reviews/{review_id}", headers=headers
                        )
                        if verify.status_code != 404:
                            raise ContractUploadGateError("删除后任务仍可查询")
                        for item in reversed(file_results):
                            if item.get("format") == label:
                                item["deleted"] = True
                                break
                        _progress(f"[{label}] deleted and verified")
                    except (ContractUploadGateError, httpx.HTTPError) as error:
                        errors.append(f"{label} 删除回归失败：{error}")

        _progress("[gate] checking contract history after cleanup")
        try:
            history_after = _history_check(
                client,
                base_url,
                headers,
                privacy_sentinels=args.privacy_sentinel,
            )
        except ContractUploadGateError as error:
            history_after = {"status": "failed"}
            errors.append(f"迁移 API 回归：合同历史复查失败（{error}）")

    return {
        "format_version": 1,
        "status": "passed" if not errors and len(file_results) == len(cases) else "failed",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": {
            "formats": sorted(item["format"] for item in file_results if item.get("status") != "failed"),
            "history_before": history_before,
            "history_after": history_after,
            "deletion_checked": not args.keep_reviews,
            "privacy_sentinels_checked": len(args.privacy_sentinel),
            "external_ocr_allowed": args.allow_external_ocr,
        },
        "files": file_results,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "errors": errors,
    }


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = run_gate(args)
    except (ContractUploadGateError, OSError, httpx.HTTPError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
