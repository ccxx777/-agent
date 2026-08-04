#!/usr/bin/env python3
"""Contract review end-to-end acceptance gate.

This gate intentionally exercises the public API only.  It uses a synthetic or
redacted contract and verifies the complete product path:

    upload -> extraction -> fact confirmation -> workflow -> report JSON/PDF
    -> report-scoped chat -> session review list -> deletion

The script never writes contract text or model responses to its output.  The
confirmation values used to unblock a synthetic test contract are explicitly
test values and must not be interpreted as legal advice or user decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

try:  # Package import when invoked by pytest or ``python -m``.
    from evaluation.contract_upload_api_smoke import (
        ALLOWED_SUFFIXES,
        MIME_TYPES,
        review_is_terminal,
        validate_public_detail,
        validate_public_payload,
    )
except ModuleNotFoundError:  # Direct ``python evaluation/contract_review_e2e.py``.
    from contract_upload_api_smoke import (  # type: ignore[no-redef]
        ALLOWED_SUFFIXES,
        MIME_TYPES,
        review_is_terminal,
        validate_public_detail,
        validate_public_payload,
    )


class ContractReviewE2EError(RuntimeError):
    """The end-to-end contract review gate failed."""


# A labor-contract E2E gate must not silently accept an out-of-scope or failed
# workflow.  ``partial`` is allowed because the legal corpus can be pending
# activation while the report persistence and chat path still work.
ALLOWED_WORKFLOW_STATUSES = {"completed", "partial"}
ALLOWED_LEGAL_ACTIVATION_STATUSES = {"ACTIVE", "PENDING_LEGAL_REVIEW"}
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


def _progress(message: str) -> None:
    """Keep progress on stderr so stdout remains a machine-readable summary."""

    print(message, file=sys.stderr, flush=True)


def _headers(token: str) -> dict[str, str]:
    if not token:
        raise ContractReviewE2EError(
            "缺少 Token，请设置 --token、CONTRACT_TEST_TOKEN 或 TOKEN"
        )
    return {"Authorization": f"Bearer {token}"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="合同上传到报告问答的端到端 API 验收门禁"
    )
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument(
        "--base-url",
        default=_env("BACKEND_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--token",
        default=_env("CONTRACT_TEST_TOKEN", _env("TOKEN")),
    )
    parser.add_argument(
        "--session-id",
        help="可选：把合同绑定到已有 UUID 会话；不提供时由后端创建新会话",
    )
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--http-timeout-seconds",
        type=float,
        default=300.0,
        help="单个 API 请求的超时时间，Workflow 可能比普通查询耗时更长",
    )
    parser.add_argument(
        "--resolution-policy",
        choices=("supplement", "not_applicable"),
        default="supplement",
        help="如何自动解决测试合同中的未确认事实；仅用于合成/脱敏测试",
    )
    parser.add_argument(
        "--supplement-value",
        default="端到端测试补充值",
        help="resolution-policy=supplement 时使用的非敏感测试值",
    )
    parser.add_argument(
        "--allow-external-ocr",
        action="store_true",
        help="允许 PDF 测试的 external_raw_image_sent=true",
    )
    parser.add_argument(
        "--expect-external-ocr",
        action="store_true",
        help="严格要求本次测试的 external_raw_image_sent=true；必须同时允许外部 OCR",
    )
    parser.add_argument(
        "--ack-test-confirmation-writes",
        action="store_true",
        help="确认本次只使用脱敏/自拟合同，并允许写入测试确认值、报告和会话消息",
    )
    parser.add_argument(
        "--privacy-sentinel",
        action="append",
        default=[],
        help="必须不出现在任何 API JSON 的敏感值，可重复传入",
    )
    parser.add_argument(
        "--keep-review",
        action="store_true",
        help="保留测试任务；默认删除并验证删除后的 404",
    )
    parser.add_argument(
        "--require-legal-citations",
        action="store_true",
        help="要求报告包含可引用的法律来源；用于法律 Workflow 回归",
    )
    parser.add_argument(
        "--legal-source-level",
        choices=("A", "B"),
        default="A",
        help="法律引用回归要求的来源等级，默认 A",
    )
    parser.add_argument(
        "--legal-official-url-prefix",
        default="https://flk.npc.gov.cn/",
        help="法律引用必须使用的官方来源 URL 前缀",
    )
    parser.add_argument(
        "--allow-pending-legal-governance",
        action="store_true",
        help="staging 允许 PENDING_LEGAL_REVIEW；生产回归不要使用",
    )
    parser.add_argument("--output", type=Path)
    return parser


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise ContractReviewE2EError(
            f"HTTP {response.status_code} {response.request.method} "
            f"{response.request.url.path}"
        ) from error
    try:
        payload = response.json()
    except ValueError as error:
        raise ContractReviewE2EError("Backend 返回的不是 JSON") from error
    if not isinstance(payload, dict):
        raise ContractReviewE2EError("Backend JSON 顶层不是对象")
    return payload


def _private_keys(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text in PRIVATE_KEYS:
                found.append(child_path)
            found.extend(_private_keys(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_private_keys(item, f"{path}[{index}]"))
    return found


def _privacy_errors(value: Any, sentinels: list[str]) -> list[str]:
    errors = validate_public_payload(value, privacy_sentinels=sentinels)
    if _private_keys(value):
        errors.append("public response contains private fields")
    return list(dict.fromkeys(errors))


def _validate_file(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ContractReviewE2EError(f"合同文件不存在：{resolved}")
    if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ContractReviewE2EError("合同文件必须是 PDF、DOC 或 DOCX")
    return resolved


def _review_id_from_upload(created: dict[str, Any]) -> str:
    """Extract the cleanup handle before inspecting the rest of the response."""

    review_id = created.get("review_id")
    if not isinstance(review_id, str) or not review_id:
        raise ContractReviewE2EError("上传响应缺少 review_id，无法建立清理句柄")
    return review_id


def _require_test_confirmation_acknowledgement(acknowledged: bool) -> None:
    if not acknowledged:
        raise ContractReviewE2EError(
            "端到端门禁会写入测试确认值并生成报告；请仅使用脱敏/自拟合同，"
            "并显式传入 --ack-test-confirmation-writes"
        )


def _external_ocr_expectation_error(
    detail: dict[str, Any], *, expect_external_ocr: bool
) -> str | None:
    if not expect_external_ocr:
        return None
    privacy = detail.get("privacy")
    if not isinstance(privacy, dict) or privacy.get("external_raw_image_sent") is not True:
        return "本次测试预期外部 OCR，但 external_raw_image_sent 不为 true"
    return None


def _extract_pdf_text_for_privacy(pdf_bytes: bytes) -> str:
    """Extract generated-report text in memory so encoded PDFs cannot bypass the gate."""

    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ContractReviewE2EError(
            "PDF 隐私哨兵检查需要 pypdf；请使用 evaluation/requirements.txt 运行"
        ) from error
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise ContractReviewE2EError("无法提取报告 PDF 文本，隐私门禁不能判定") from error
    if not text.strip():
        raise ContractReviewE2EError("报告 PDF 没有可提取文本，隐私门禁不能判定")
    return text


def _confirmation_items(
    confirmation: dict[str, Any],
    *,
    policy: str,
    supplement_value: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Create deterministic, test-only actions for unresolved facts."""

    facts = confirmation.get("facts")
    unresolved = confirmation.get("unresolved_questions")
    if not isinstance(facts, list) or not isinstance(unresolved, list):
        raise ContractReviewE2EError("确认响应缺少 facts 或 unresolved_questions")

    unresolved_ids = {
        item.get("fact_id")
        for item in unresolved
        if isinstance(item, dict) and isinstance(item.get("fact_id"), str)
    }
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("fact_id") not in unresolved_ids:
            continue
        fact_id = str(fact["fact_id"])
        allowed = set(fact.get("allowed_actions") or [])
        action: str | None = None
        item: dict[str, Any] = {"fact_id": fact_id}

        # A fact with usable contract evidence can be confirmed without inventing
        # a value.  For genuinely unresolved facts, use an explicit test policy.
        if "confirm" in allowed:
            action = "confirm"
        elif policy == "supplement" and "supplement" in allowed:
            action = "supplement"
            item["value"] = supplement_value
            item["note"] = "端到端测试自动补充，不代表用户事实"
        elif "not_applicable" in allowed:
            action = "not_applicable"
            item["note"] = "端到端测试标记，不代表真实合同判断"
        # ``defer`` intentionally is not selected automatically: it leaves the
        # confirmation gate unresolved and would make this acceptance test look
        # successful while no workflow could legally start.
        if action is None:
            raise ContractReviewE2EError(
                f"事实 {fact_id} 没有可用的自动化确认动作"
            )
        item["action"] = action
        items.append(item)
        counts[action] = counts.get(action, 0) + 1

    selected_ids = {str(item["fact_id"]) for item in items}
    missing_ids = sorted(str(fact_id) for fact_id in unresolved_ids - selected_ids)
    if missing_ids:
        raise ContractReviewE2EError(
            "确认表单存在无法自动解决的事实：" + ", ".join(missing_ids)
        )

    if not items:
        # A fully confirmed extraction can still have a not_started snapshot;
        # submit one safe confirmation action to exercise the persistence path.
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            allowed = set(fact.get("allowed_actions") or [])
            if "confirm" in allowed:
                items = [{"fact_id": str(fact["fact_id"]), "action": "confirm"}]
                counts["confirm"] = 1
                break
    return items, counts


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    """Keep report verification output metadata-only."""

    findings = report.get("findings")
    legal_sources = report.get("legal_sources")
    case_sources = report.get("case_sources")
    pending_questions = report.get("pending_questions")
    return {
        "report_id": report.get("report_id"),
        "review_id": report.get("review_id"),
        "session_id": report.get("session_id"),
        "workflow_status": report.get("workflow_status"),
        "findings": len(findings) if isinstance(findings, list) else 0,
        "legal_sources": len(legal_sources) if isinstance(legal_sources, list) else 0,
        "case_sources": len(case_sources) if isinstance(case_sources, list) else 0,
        "pending_questions": (
            len(pending_questions) if isinstance(pending_questions, list) else 0
        ),
    }


def validate_legal_citations(
    report: dict[str, Any],
    *,
    source_level: str = "A",
    official_url_prefix: str = "https://flk.npc.gov.cn/",
    allow_pending_governance: bool = False,
) -> list[str]:
    """Validate legal citation fields without returning citation text.

    The contract E2E gate normally checks persistence and chat only. When the
    legal-citation mode is enabled, this helper verifies that the report came
    from the isolated legal collection and that every source is usable for a
    traceable citation. It deliberately reports only source indexes/field
    names so a failed gate cannot leak legal document text into CI logs.
    """

    errors: list[str] = []
    sources = report.get("legal_sources")
    if not isinstance(sources, list) or not sources:
        return ["报告没有 legal_sources，无法证明法律检索链路已接入"]

    expected_prefix = official_url_prefix.strip()
    if not expected_prefix:
        return ["法律引用门禁缺少 official URL 前缀"]

    for index, source in enumerate(sources, 1):
        label = f"legal_sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label} 不是对象")
            continue

        if source.get("source_level") != source_level:
            errors.append(f"{label}.source_level 不是 {source_level}")
        if source.get("citation_eligible") is not True:
            errors.append(f"{label}.citation_eligible 不是 true")

        rank = source.get("rank")
        if not isinstance(rank, int) or rank < 1:
            errors.append(f"{label}.rank 无效")

        official_url = str(source.get("official_url") or "").strip()
        if not official_url.startswith(expected_prefix):
            errors.append(f"{label}.official_url 不是指定官方来源")

        if not str(source.get("effective_date") or "").strip():
            errors.append(f"{label}.effective_date 缺失")

        citation_label = str(source.get("citation_label") or "").strip()
        if not citation_label:
            errors.append(f"{label}.citation_label 缺失")
        elif source_level == "A" and (
            "第" not in citation_label or "条" not in citation_label
        ):
            errors.append(f"{label}.citation_label 缺少法条编号")

        if not str(source.get("quote") or "").strip():
            errors.append(f"{label}.quote 缺失")

        activation_status = str(source.get("legal_activation_status") or "").strip()
        if activation_status not in ALLOWED_LEGAL_ACTIVATION_STATUSES:
            errors.append(f"{label}.legal_activation_status 无效")
        elif not allow_pending_governance and activation_status != "ACTIVE":
            errors.append(
                f"{label}.legal_activation_status 仍为 {activation_status}，正式回归要求 ACTIVE"
            )

    return errors


def _poll_review(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    review_id: str,
    poll_seconds: float,
    timeout_seconds: float,
    sentinels: list[str],
    allow_external_ocr: bool,
    expect_external_ocr: bool,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    poll_count = 0
    terminal_not_started_polls = 0
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        poll_count += 1
        detail = _json_response(
            client.get(f"{base_url}/api/contract-reviews/{review_id}", headers=headers)
        )
        privacy_errors = _privacy_errors(detail, sentinels)
        if privacy_errors:
            raise ContractReviewE2EError(
                "任务详情越过隐私边界：" + ", ".join(privacy_errors)
            )
        if (
            detail.get("status") in {"ready", "needs_confirmation"}
            and detail.get("extraction_status") == "not_started"
        ):
            terminal_not_started_polls += 1
            if terminal_not_started_polls >= 2:
                raise ContractReviewE2EError(
                    "文件解析已完成但事实提取未启动；请确认 CONTRACT_EXTRACTION_ENABLED=true "
                    "且模型服务配置可用"
                )
        else:
            terminal_not_started_polls = 0
        if review_is_terminal(detail, require_extraction=True):
            if detail.get("status") == "failed":
                raise ContractReviewE2EError("合同文件解析失败")
            final_errors = validate_public_detail(
                detail,
                privacy_sentinels=sentinels,
                expected_redactions={},
                require_extraction=True,
                allow_external_ocr=allow_external_ocr,
            )
            if final_errors:
                raise ContractReviewE2EError("任务详情校验失败：" + ", ".join(final_errors))
            ocr_error = _external_ocr_expectation_error(
                detail,
                expect_external_ocr=expect_external_ocr,
            )
            if ocr_error:
                raise ContractReviewE2EError(ocr_error)
            _progress(
                f"[e2e] extraction terminal status={detail.get('status')} "
                f"extraction={detail.get('extraction_status')} polls={poll_count}"
            )
            detail["_poll_count"] = poll_count
            return detail
        if poll_count == 1 or poll_count % 5 == 0:
            _progress(
                f"[e2e] poll={poll_count} status={detail.get('status')} "
                f"extraction={detail.get('extraction_status')}"
            )
        time.sleep(max(0.1, poll_seconds))
    raise ContractReviewE2EError(
        f"等待事实提取超时（{timeout_seconds:.0f}s），review_id={review_id}"
    )


def run_e2e(args: argparse.Namespace) -> dict[str, Any]:
    file_path = _validate_file(args.file)
    if not args.supplement_value.strip():
        raise ContractReviewE2EError("--supplement-value 不能为空")
    base_url = args.base_url.rstrip("/")
    headers = _headers(args.token)
    started = time.monotonic()
    review_id: str | None = None
    session_id: str | None = None
    deleted = False
    action_counts: dict[str, int] = {}
    report_summary: dict[str, Any] = {}
    legal_citation_summary: dict[str, Any] = {"required": False}
    chat_summary: dict[str, Any] = {}
    pdf_bytes = 0
    poll_count = 0
    _require_test_confirmation_acknowledgement(args.ack_test_confirmation_writes)
    if args.expect_external_ocr and not args.allow_external_ocr:
        raise ContractReviewE2EError(
            "--expect-external-ocr 必须与 --allow-external-ocr 一起使用"
        )

    with httpx.Client(timeout=max(1.0, args.http_timeout_seconds)) as client:
        primary_error: Exception | None = None
        try:
            form_data: dict[str, str] = {"retention_policy": "short"}
            if args.session_id:
                form_data["session_id"] = args.session_id
            _progress(f"[e2e] uploading {file_path.name}")
            with file_path.open("rb") as handle:
                created = _json_response(
                    client.post(
                        f"{base_url}/api/contract-reviews",
                        headers=headers,
                        data=form_data,
                        files={
                            "file": (
                                file_path.name,
                                handle,
                                MIME_TYPES[file_path.suffix.lower()],
                            )
                        },
                    )
                )
            review_id = _review_id_from_upload(created)
            created_privacy_errors = _privacy_errors(created, args.privacy_sentinel)
            if created_privacy_errors:
                raise ContractReviewE2EError(
                    "上传响应越过隐私边界：" + ", ".join(created_privacy_errors)
                )
            _progress(f"[e2e] review_id={review_id}")

            detail = _poll_review(
                client,
                base_url=base_url,
                headers=headers,
                review_id=review_id,
                poll_seconds=args.poll_seconds,
                timeout_seconds=args.timeout_seconds,
                sentinels=args.privacy_sentinel,
                allow_external_ocr=args.allow_external_ocr,
                expect_external_ocr=args.expect_external_ocr,
            )
            poll_count = int(detail.get("_poll_count") or 0)
            session_id = detail.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise ContractReviewE2EError("合同任务缺少 session_id")

            confirmation = _json_response(
                client.get(
                    f"{base_url}/api/contract-reviews/{review_id}/confirmation",
                    headers=headers,
                )
            )
            privacy_errors = _privacy_errors(confirmation, args.privacy_sentinel)
            if privacy_errors:
                raise ContractReviewE2EError(
                    "确认表单越过隐私边界：" + ", ".join(privacy_errors)
                )
            items, action_counts = _confirmation_items(
                confirmation,
                policy=args.resolution_policy,
                supplement_value=args.supplement_value,
            )
            if not items:
                raise ContractReviewE2EError("确认表单没有可提交的事实操作")
            _progress(
                f"[e2e] confirming facts={len(items)} actions={action_counts}"
            )
            confirmed = _json_response(
                client.put(
                    f"{base_url}/api/contract-reviews/{review_id}/confirmation",
                    headers=headers,
                    json={
                        "base_revision": int(confirmation.get("confirmation_revision") or 0),
                        "items": items,
                        "submit": True,
                        "request_id": str(uuid4()),
                    },
                )
            )
            if _privacy_errors(confirmed, args.privacy_sentinel):
                raise ContractReviewE2EError("确认提交响应越过隐私边界")
            if confirmed.get("ready_for_legal_review") is not True:
                unresolved = confirmed.get("unresolved_questions") or []
                raise ContractReviewE2EError(
                    f"确认门禁未通过，剩余问题数={len(unresolved)}"
                )

            _progress("[e2e] running contract review workflow")
            workflow = _json_response(
                client.post(
                    f"{base_url}/api/contract-reviews/{review_id}/workflow",
                    headers=headers,
                )
            )
            if _privacy_errors(workflow, args.privacy_sentinel):
                raise ContractReviewE2EError("Workflow 响应越过隐私边界")
            report = workflow.get("report")
            if not isinstance(report, dict):
                raise ContractReviewE2EError("Workflow 响应缺少 report")
            workflow_status = workflow.get("workflow_status")
            if workflow_status not in ALLOWED_WORKFLOW_STATUSES:
                raise ContractReviewE2EError(
                    f"Workflow 状态无效：{workflow_status!r}"
                )
            report_id = workflow.get("report_id")
            if not isinstance(report_id, str) or not report_id:
                raise ContractReviewE2EError("Workflow 响应缺少持久化 report_id")

            persisted_report = _json_response(
                client.get(
                    f"{base_url}/api/contract-reviews/{review_id}/report",
                    headers=headers,
                )
            )
            if _privacy_errors(persisted_report, args.privacy_sentinel):
                raise ContractReviewE2EError("报告 JSON 越过隐私边界")
            if persisted_report.get("report_id") != report_id:
                raise ContractReviewE2EError("报告 JSON 与 Workflow report_id 不一致")
            if persisted_report.get("review_id") != review_id:
                raise ContractReviewE2EError("报告 JSON 与 review_id 不一致")
            if persisted_report.get("session_id") != session_id:
                raise ContractReviewE2EError("报告没有保持上传会话 session_id")
            if args.require_legal_citations:
                legal_errors = validate_legal_citations(
                    persisted_report,
                    source_level=args.legal_source_level,
                    official_url_prefix=args.legal_official_url_prefix,
                    allow_pending_governance=args.allow_pending_legal_governance,
                )
                if legal_errors:
                    raise ContractReviewE2EError(
                        "法律引用门禁失败：" + "; ".join(legal_errors)
                    )
                legal_sources = persisted_report.get("legal_sources") or []
                legal_citation_summary = {
                    "required": True,
                    "source_level": args.legal_source_level,
                    "sources": len(legal_sources),
                    "activation_statuses": sorted(
                        {
                            str(source.get("legal_activation_status") or "")
                            for source in legal_sources
                            if isinstance(source, dict)
                        }
                    ),
                    "official_url_prefix": args.legal_official_url_prefix,
                    "all_fields_valid": True,
                }
            report_summary = summarize_report(persisted_report)

            pdf_response = client.get(
                f"{base_url}/api/contract-reviews/{review_id}/report.pdf",
                headers=headers,
            )
            try:
                pdf_response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise ContractReviewE2EError("报告 PDF 下载失败") from error
            content_type = pdf_response.headers.get("content-type", "").lower()
            if "application/pdf" not in content_type or not pdf_response.content.startswith(b"%PDF"):
                raise ContractReviewE2EError("报告 PDF 响应格式无效")
            pdf_text = (
                _extract_pdf_text_for_privacy(pdf_response.content)
                if args.privacy_sentinel
                else ""
            )
            for index, sentinel in enumerate(args.privacy_sentinel):
                if sentinel and sentinel in pdf_text:
                    raise ContractReviewE2EError(
                        f"报告 PDF 包含第 {index + 1} 个隐私哨兵"
                    )
            pdf_bytes = len(pdf_response.content)

            chat = _json_response(
                client.post(
                    f"{base_url}/api/chat",
                    headers=headers,
                    json={
                        "query": "请概括这份合同风险报告中的待确认事项。",
                        "session_id": session_id,
                        "mode": "contract_review",
                        "review_id": review_id,
                    },
                )
            )
            if _privacy_errors(chat, args.privacy_sentinel):
                raise ContractReviewE2EError("报告问答响应越过隐私边界")
            if chat.get("session_id") != session_id:
                raise ContractReviewE2EError("报告问答没有保持上传会话 session_id")
            if not isinstance(chat.get("answer"), str) or not chat["answer"].strip():
                raise ContractReviewE2EError("报告问答返回空答案")
            chat_summary = {
                "status": "passed",
                "session_id": session_id,
                "answer_characters": len(chat["answer"]),
            }

            history = _json_response(
                client.get(f"{base_url}/api/chat/history/{session_id}", headers=headers)
            )
            if _privacy_errors(history, args.privacy_sentinel):
                raise ContractReviewE2EError("会话历史越过隐私边界")
            messages = history.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ContractReviewE2EError("报告问答后会话历史为空")

            session_reviews = _json_response(
                client.get(
                    f"{base_url}/api/sessions/{session_id}/reviews",
                    headers=headers,
                )
            )
            if _privacy_errors(session_reviews, args.privacy_sentinel):
                raise ContractReviewE2EError("会话合同列表越过隐私边界")
            review_rows = session_reviews.get("reviews")
            if not isinstance(review_rows, list) or not any(
                isinstance(row, dict) and row.get("review_id") == review_id
                for row in review_rows
            ):
                raise ContractReviewE2EError("会话合同列表没有当前 review_id")
            _progress("[e2e] report JSON/PDF/chat/session checks passed")
        except Exception as error:
            primary_error = error
            raise
        finally:
            if review_id and not args.keep_review:
                _progress("[e2e] deleting review and verifying 404")
                try:
                    delete_response = client.delete(
                        f"{base_url}/api/contract-reviews/{review_id}", headers=headers
                    )
                    if delete_response.status_code not in {204, 404}:
                        raise ContractReviewE2EError(
                            f"删除合同任务失败：HTTP {delete_response.status_code}"
                        )
                    verify = client.get(
                        f"{base_url}/api/contract-reviews/{review_id}", headers=headers
                    )
                    if verify.status_code != 404:
                        raise ContractReviewE2EError("删除后合同任务仍可查询")
                    deleted = True
                except (ContractReviewE2EError, httpx.HTTPError) as cleanup_error:
                    if primary_error is not None:
                        _progress(
                            f"[e2e] cleanup failed after primary error; review_id={review_id}: "
                            f"{cleanup_error}"
                        )
                    else:
                        raise

    return {
        "format_version": 1,
        "status": "passed",
        "file_format": file_path.suffix.lower().removeprefix("."),
        "review_id": review_id,
        "session_id": session_id,
        "poll_count": poll_count,
        "confirmation": {
            "actions": action_counts,
            "ready_for_legal_review": True,
        },
        "workflow": report_summary,
        "legal_citations": legal_citation_summary,
        "report_pdf_bytes": pdf_bytes,
        "report_chat": chat_summary,
        "deletion_checked": deleted,
        "external_ocr_allowed": args.allow_external_ocr,
        "external_ocr_expected": args.expect_external_ocr,
        "test_confirmation_writes_acknowledged": args.ack_test_confirmation_writes,
        "privacy_sentinels_checked": len(args.privacy_sentinel),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = run_e2e(args)
    except (ContractReviewE2EError, OSError, httpx.HTTPError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
