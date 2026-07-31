#!/usr/bin/env python3
"""上传劳动合同并轮询事实提取结果的端到端 Smoke Test。

脚本只输出任务状态、质量统计、脱敏统计和提取摘要，不打印合同页文本、LLM
Prompt、模型返回原文或任何敏感值。它适合在服务器上验证：上传接口、异步解析、
自适应 single/batch 提取、必备字段覆盖和 JSON 结果是否正常串起来。
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


class ContractReviewSmokeError(RuntimeError):
    """合同 Smoke Test 配置或运行失败。"""


TERMINAL_EXTRACTION_STATUSES = {"ready", "needs_confirmation", "failed"}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="劳动合同上传与事实提取 Smoke Test")
    parser.add_argument("--file", type=Path, required=True, help="待上传的 PDF、DOC 或 DOCX")
    parser.add_argument("--base-url", default=_env("BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--token",
        default=_env("CONTRACT_TEST_TOKEN", _env("TOKEN")),
        help="Bearer Token；也可通过 CONTRACT_TEST_TOKEN 或 TOKEN 提供",
    )
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--expect-mode",
        choices=("single", "batch"),
        help="可选：要求最终 extraction_mode 与指定值一致",
    )
    parser.add_argument("--output", type=Path, help="可选：保存不含合同正文的摘要 JSON")
    return parser


def _headers(token: str) -> dict[str, str]:
    if not token:
        raise ContractReviewSmokeError("缺少 Token，请设置 --token、CONTRACT_TEST_TOKEN 或 TOKEN")
    return {"Authorization": f"Bearer {token}"}


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = response.text[:500]
        raise ContractReviewSmokeError(
            f"HTTP {response.status_code} {response.request.method} {response.request.url}: {detail}"
        ) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise ContractReviewSmokeError("Backend 返回的不是 JSON") from error
    if not isinstance(payload, dict):
        raise ContractReviewSmokeError("Backend JSON 顶层不是对象")
    return payload


def _safe_quality(detail: dict[str, Any]) -> dict[str, Any] | None:
    quality = detail.get("quality")
    if not isinstance(quality, dict):
        return None
    fields = (
        "page_count",
        "text_pages",
        "native_pages",
        "hybrid_pages",
        "scanned_pages",
        "ocr_pages",
        "failed_pages",
        "suspicious_pages",
        "text_coverage",
        "needs_confirmation",
    )
    return {field: quality.get(field) for field in fields if field in quality}


def _safe_privacy(detail: dict[str, Any]) -> dict[str, Any] | None:
    privacy = detail.get("privacy")
    if not isinstance(privacy, dict):
        return None
    return {
        "redaction_version": privacy.get("redaction_version"),
        "redaction_counts": privacy.get("redaction_counts", {}),
        "zero_width_sequences_detected": privacy.get("zero_width_sequences_detected", 0),
        "external_raw_image_sent": privacy.get("external_raw_image_sent", False),
    }


def summarize_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """只保留可公开记录的任务和提取摘要，不带 pages/context_text。"""

    extraction = detail.get("extraction")
    if not isinstance(extraction, dict):
        extraction = {}
    facts = extraction.get("facts")
    clauses = extraction.get("clauses")
    return {
        "format_version": 1,
        "review_id": detail.get("review_id"),
        "filename": detail.get("filename"),
        "status": detail.get("status"),
        "extraction_status": detail.get("extraction_status"),
        "confirmation_status": detail.get("confirmation_status"),
        "quality": _safe_quality(detail),
        "privacy": _safe_privacy(detail),
        "extraction": {
            "extraction_mode": extraction.get("extraction_mode"),
            "model_calls": extraction.get("model_calls", 0),
            "invalid_fact_count": extraction.get("invalid_fact_count", 0),
            "clauses": len(clauses) if isinstance(clauses, list) else 0,
            "facts": len(facts) if isinstance(facts, list) else 0,
            "missing_required_fields": extraction.get("missing_required_fields", []),
            "confirmation_questions": len(extraction.get("confirmation_questions", [])),
            "warnings": extraction.get("warnings", []),
        },
    }


def validate_detail(detail: dict[str, Any], expect_mode: str | None = None) -> list[str]:
    """检查端到端结果契约；needs_confirmation 是正常的人工确认状态。"""

    errors: list[str] = []
    extraction_status = detail.get("extraction_status")
    if detail.get("status") == "failed":
        errors.append("文件解析状态为 failed")
    if extraction_status not in TERMINAL_EXTRACTION_STATUSES:
        errors.append(f"事实提取尚未进入终态：{extraction_status!r}")
        return errors
    if extraction_status == "failed":
        errors.append("事实提取状态为 failed")

    extraction = detail.get("extraction")
    if not isinstance(extraction, dict):
        errors.append("终态响应缺少 extraction 对象")
        return errors
    mode = extraction.get("extraction_mode")
    if mode not in {"single", "batch"}:
        errors.append(f"extraction_mode 无效：{mode!r}")
    if expect_mode and mode != expect_mode:
        errors.append(f"extraction_mode={mode!r}，预期为 {expect_mode!r}")
    model_calls = extraction.get("model_calls", 0)
    if not isinstance(model_calls, int) or model_calls < 0:
        errors.append("model_calls 不是非负整数")
    invalid_count = extraction.get("invalid_fact_count", 0)
    if not isinstance(invalid_count, int) or invalid_count < 0:
        errors.append("invalid_fact_count 不是非负整数")
    missing = extraction.get("missing_required_fields", [])
    if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
        errors.append("missing_required_fields 不是字符串数组")
    facts = extraction.get("facts", [])
    if not isinstance(facts, list):
        errors.append("facts 不是数组")
    return errors


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    file_path = args.file.expanduser().resolve()
    if not file_path.is_file():
        raise ContractReviewSmokeError(f"合同文件不存在：{file_path}")
    if file_path.suffix.lower() not in {".pdf", ".doc", ".docx"}:
        raise ContractReviewSmokeError("合同文件必须是 PDF、DOC 或 DOCX")

    base_url = args.base_url.rstrip("/")
    headers = _headers(args.token)
    started = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        with file_path.open("rb") as handle:
            upload = client.post(
                f"{base_url}/api/contract-reviews",
                headers=headers,
                files={"file": (file_path.name, handle, "application/octet-stream")},
            )
        created = _json_response(upload)
        review_id = created.get("review_id")
        if not isinstance(review_id, str) or not review_id:
            raise ContractReviewSmokeError("上传响应缺少 review_id")
        print(f"uploaded review_id={review_id} status={created.get('status')}", flush=True)

        detail: dict[str, Any] = {}
        poll_count = 0
        deadline = time.monotonic() + max(1.0, args.timeout_seconds)
        while time.monotonic() < deadline:
            poll_count += 1
            detail = _json_response(
                client.get(
                    f"{base_url}/api/contract-reviews/{review_id}",
                    headers=headers,
                )
            )
            status = detail.get("status")
            extraction_status = detail.get("extraction_status")
            print(
                f"poll={poll_count} status={status} extraction={extraction_status}",
                flush=True,
            )
            if extraction_status in TERMINAL_EXTRACTION_STATUSES:
                break
            time.sleep(max(0.1, args.poll_seconds))
        else:
            raise ContractReviewSmokeError(
                f"等待事实提取超时（{args.timeout_seconds:.0f}s），review_id={review_id}"
            )

    errors = validate_detail(detail, args.expect_mode)
    summary = summarize_detail(detail)
    summary.update(
        {
            "status": "passed" if not errors else "failed",
            "poll_count": poll_count,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "errors": errors,
        }
    )
    return summary


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = run_smoke(args)
    except (ContractReviewSmokeError, OSError, httpx.HTTPError) as error:
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
