"""watsonxDocsQA 答案生成结果统计与人工抽查材料生成。

本模块位于答案生成与 RAGAS 之间，承担质量门职责：

1. 校验固定题集是否完整生成；
2. 统计召回命中、拒答、引用、上下文和延迟；
3. 优先选择高风险样本，并生成包含完整证据的 Markdown 报告；
4. 用输入与报告哈希锁定人工确认，避免答案变化后误用旧审批。

这里只做确定性的离线检查，不调用 LLM，也不修改召回或生成结果。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


FORMAT_VERSION = 1
SUMMARY_FILENAME = "review_summary.json"
SPOTCHECK_JSON_FILENAME = "spotcheck.json"
SPOTCHECK_MARKDOWN_FILENAME = "spotcheck.md"
MANIFEST_FILENAME = "review_manifest.json"
APPROVAL_FILENAME = "approval.json"

CITATION_PATTERN = re.compile(r"\[(\d+)]")
CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
REFUSAL_PATTERNS = (
    "知识库中未找到",
    "未找到相关信息",
    "没有找到相关信息",
    "无法从参考资料",
    "无法根据参考资料",
    "insufficient information",
    "not enough information",
    "cannot answer",
    "can't answer",
)


class ReviewError(RuntimeError):
    """生成结果或人工确认不满足完整基线约束。"""


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_generation_rows(path: Path, *, expected_questions: int) -> list[dict[str, Any]]:
    """读取完整生成结果，并校验人工审查所需字段。"""

    if not path.is_file():
        raise ReviewError(f"生成结果不存在：{path}")
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
                raise ReviewError(f"生成结果第 {row_number} 行不是合法 JSON") from error
            question_id = str(row.get("question_id") or "").strip()
            if not question_id:
                raise ReviewError(f"生成结果第 {row_number} 行缺少 question_id")
            if question_id in seen:
                raise ReviewError(f"生成结果存在重复 question_id：{question_id}")
            seen.add(question_id)
            for field in ("question", "reference_answer", "answer"):
                if not str(row.get(field) or "").strip():
                    raise ReviewError(f"{question_id} 的 {field} 为空")
            for field in ("gold_doc_ids", "contexts", "documents"):
                if not isinstance(row.get(field), list):
                    raise ReviewError(f"{question_id} 的 {field} 不是数组")
            rows.append(row)
    if len(rows) != expected_questions:
        raise ReviewError(
            f"生成结果为 {len(rows)} 题，完整基线要求 {expected_questions} 题"
        )
    expected_ids = {f"test_{index}" for index in range(1, expected_questions + 1)}
    actual_ids = {str(row["question_id"]) for row in rows}
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        raise ReviewError(f"题目 ID 不完整：missing={missing}, unexpected={unexpected}")
    return sorted(rows, key=lambda row: int(str(row["question_id"]).split("_")[-1]))


def selected_input_sha256(rows: Iterable[dict[str, Any]]) -> str:
    """只签名会影响人工判断和 RAGAS 的字段。"""

    canonical = [
        {
            "question_id": row["question_id"],
            "question": row["question"],
            "reference_answer": row["reference_answer"],
            "gold_doc_ids": row["gold_doc_ids"],
            "answer": row["answer"],
            "contexts": row["contexts"],
            "documents": row["documents"],
        }
        for row in rows
    ]
    return _sha256_bytes(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 6) if values else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 6)


def _is_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(pattern.lower() in lowered for pattern in REFUSAL_PATTERNS)


def inspect_row(row: dict[str, Any]) -> dict[str, Any]:
    """提取单题可复核风险信号，不把启发式信号当成质量分数。"""

    answer = str(row["answer"])
    contexts = [str(item) for item in row["contexts"]]
    documents = row["documents"]
    gold_ids = {str(item) for item in row["gold_doc_ids"]}
    retrieved_ids = [str(item.get("doc_id") or "") for item in documents]
    first_gold_rank = next(
        (index for index, doc_id in enumerate(retrieved_ids, 1) if doc_id in gold_ids),
        None,
    )
    citations = [int(value) for value in CITATION_PATTERN.findall(answer)]
    invalid_citations = sorted({value for value in citations if value < 1 or value > len(contexts)})
    refusal = _is_refusal(answer)
    reasons: list[str] = []
    if first_gold_rank is None:
        reasons.append("gold_not_in_top3")
    if refusal:
        reasons.append("refusal_answer")
    if contexts and not citations:
        reasons.append("missing_citation")
    if invalid_citations:
        reasons.append("invalid_citation")
    if len(contexts) != 3:
        reasons.append("context_count_not_3")
    if len(answer) < 50:
        reasons.append("short_answer")
    escaped_newline_contexts = sum("\\n" in context for context in contexts)
    if escaped_newline_contexts:
        reasons.append("literal_escaped_newlines")
    question_has_chinese = bool(CHINESE_PATTERN.search(str(row["question"])))
    answer_has_chinese = bool(CHINESE_PATTERN.search(answer))
    if question_has_chinese != answer_has_chinese:
        reasons.append("cross_language_answer")
    return {
        "question_id": row["question_id"],
        "answer_characters": len(answer),
        "context_count": len(contexts),
        "first_gold_rank": first_gold_rank,
        "hit_at_1": first_gold_rank == 1,
        "hit_at_3": first_gold_rank is not None,
        "refusal": refusal,
        "citations": citations,
        "invalid_citations": invalid_citations,
        "escaped_newline_contexts": escaped_newline_contexts,
        "risk_reasons": reasons,
        "latency_seconds": {
            name: float(row.get("latency_seconds", {}).get(name) or 0.0)
            for name in ("retrieval", "generation", "total")
        },
    }


def _select_spotchecks(
    rows: list[dict[str, Any]],
    inspections: list[dict[str, Any]],
    *,
    target: int,
) -> list[dict[str, Any]]:
    if target <= 0:
        raise ReviewError("spotcheck-target 必须大于 0")
    inspection_by_id = {item["question_id"]: item for item in inspections}
    row_by_id = {row["question_id"]: row for row in rows}
    selected: dict[str, list[str]] = {}

    def add(question_id: str, reason: str) -> None:
        selected.setdefault(question_id, [])
        if reason not in selected[question_id]:
            selected[question_id].append(reason)

    mandatory = {
        "gold_not_in_top3",
        "refusal_answer",
        "missing_citation",
        "invalid_citation",
        "context_count_not_3",
    }
    for inspection in inspections:
        for reason in inspection["risk_reasons"]:
            if reason in mandatory:
                add(inspection["question_id"], reason)

    by_latency = sorted(
        inspections,
        key=lambda item: item["latency_seconds"]["total"],
        reverse=True,
    )
    for inspection in by_latency[:2]:
        add(inspection["question_id"], "latency_outlier")
    shortest = min(inspections, key=lambda item: item["answer_characters"])
    longest = max(inspections, key=lambda item: item["answer_characters"])
    add(shortest["question_id"], "shortest_answer")
    add(longest["question_id"], "longest_answer")

    # 用固定位置补足普通样本，保证多次运行选择一致且覆盖题集前中后段。
    if len(selected) < target:
        positions = [0, len(rows) // 4, len(rows) // 2, (len(rows) * 3) // 4, len(rows) - 1]
        for position in positions:
            add(rows[position]["question_id"], "deterministic_coverage_sample")
            if len(selected) >= target:
                break
    if len(selected) < target:
        for row in rows:
            add(row["question_id"], "deterministic_coverage_sample")
            if len(selected) >= target:
                break

    ordered_ids = sorted(selected, key=lambda value: int(value.split("_")[-1]))
    result: list[dict[str, Any]] = []
    for question_id in ordered_ids:
        row = row_by_id[question_id]
        result.append(
            {
                "question_id": question_id,
                "selection_reasons": selected[question_id],
                "inspection": inspection_by_id[question_id],
                "question": row["question"],
                "reference_answer": row["reference_answer"],
                "answer": row["answer"],
                "gold_doc_ids": row["gold_doc_ids"],
                "contexts": row["contexts"],
                "documents": row["documents"],
            }
        )
    return result


def build_review(
    rows: list[dict[str, Any]],
    *,
    expected_questions: int,
    spotcheck_target: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inspections = [inspect_row(row) for row in rows]
    answer_lengths = [float(item["answer_characters"]) for item in inspections]
    context_counts = [float(item["context_count"]) for item in inspections]
    latency: dict[str, dict[str, float | None]] = {}
    for name in ("retrieval", "generation", "total"):
        values = [float(item["latency_seconds"][name]) for item in inspections]
        latency[name] = {
            "mean": _mean(values),
            "p50": _percentile(values, 0.5),
            "p95": _percentile(values, 0.95),
            "max": round(max(values), 6),
        }
    spotchecks = _select_spotchecks(
        rows,
        inspections,
        target=spotcheck_target,
    )
    summary = {
        "format_version": FORMAT_VERSION,
        "dataset": "watsonxDocsQA",
        "split": "test",
        "generated_at": datetime.now(UTC).isoformat(),
        "input_sha256": selected_input_sha256(rows),
        "questions": {
            "expected": expected_questions,
            "actual": len(rows),
            "complete": len(rows) == expected_questions,
        },
        "answers": {
            "mean_characters": _mean(answer_lengths),
            "p50_characters": _percentile(answer_lengths, 0.5),
            "min_characters": int(min(answer_lengths)),
            "max_characters": int(max(answer_lengths)),
            "refusal_count": sum(bool(item["refusal"]) for item in inspections),
            "cross_language_count": sum(
                "cross_language_answer" in item["risk_reasons"] for item in inspections
            ),
        },
        "retrieval": {
            "hit_at_1": round(sum(item["hit_at_1"] for item in inspections) / len(rows), 6),
            "hit_at_3": round(sum(item["hit_at_3"] for item in inspections) / len(rows), 6),
            "zero_hit_question_ids": [
                item["question_id"] for item in inspections if not item["hit_at_3"]
            ],
        },
        "citations": {
            "missing_question_ids": [
                item["question_id"]
                for item in inspections
                if "missing_citation" in item["risk_reasons"]
            ],
            "invalid_question_ids": [
                item["question_id"]
                for item in inspections
                if item["invalid_citations"]
            ],
        },
        "contexts": {
            "mean_per_answer": _mean(context_counts),
            "not_three_question_ids": [
                item["question_id"] for item in inspections if item["context_count"] != 3
            ],
            "literal_escaped_newline_question_ids": [
                item["question_id"]
                for item in inspections
                if item["escaped_newline_contexts"]
            ],
        },
        "latency_seconds": latency,
        "spotcheck": {
            "target": spotcheck_target,
            "selected": len(spotchecks),
            "question_ids": [item["question_id"] for item in spotchecks],
        },
        "quality_gate": {
            "status": "awaiting_human_review",
            "approval_required_before_ragas": True,
        },
    }
    spotcheck_payload = {
        "format_version": FORMAT_VERSION,
        "input_sha256": summary["input_sha256"],
        "samples": spotchecks,
    }
    return summary, spotcheck_payload


def _markdown_block(text: str) -> str:
    return f"```text\n{text.rstrip()}\n```"


def render_spotcheck_markdown(
    summary: dict[str, Any],
    spotcheck: dict[str, Any],
) -> str:
    """生成可直接由人或代码审查工具阅读的完整抽查材料。"""

    lines = [
        "# watsonxDocsQA 30题答案生成重点抽查",
        "",
        "> 本报告由确定性规则选样，不包含自动质量结论。人工确认后才能运行完整 RAGAS。",
        "",
        "## 总体统计",
        "",
        f"- 完成题数：{summary['questions']['actual']}/{summary['questions']['expected']}",
        f"- Gold Hit@1：{summary['retrieval']['hit_at_1']:.2%}",
        f"- Gold Hit@3：{summary['retrieval']['hit_at_3']:.2%}",
        f"- 拒答数量：{summary['answers']['refusal_count']}",
        f"- 缺少引用：{len(summary['citations']['missing_question_ids'])}",
        f"- 无效引用：{len(summary['citations']['invalid_question_ids'])}",
        f"- 平均总延迟：{summary['latency_seconds']['total']['mean']:.3f} 秒",
        f"- P95总延迟：{summary['latency_seconds']['total']['p95']:.3f} 秒",
        f"- 输入SHA256：`{summary['input_sha256']}`",
        "",
        "## 入选样本",
        "",
    ]
    for sample in spotcheck["samples"]:
        inspection = sample["inspection"]
        lines.extend(
            [
                f"### {sample['question_id']}",
                "",
                f"- 入选原因：{', '.join(sample['selection_reasons'])}",
                f"- Gold首次排名：{inspection['first_gold_rank'] or '未进入Top-3'}",
                f"- 拒答：{'是' if inspection['refusal'] else '否'}",
                f"- 引用：{inspection['citations'] or '无'}",
                f"- 总延迟：{inspection['latency_seconds']['total']:.3f} 秒",
                "",
                "#### 问题",
                "",
                _markdown_block(str(sample["question"])),
                "",
                "#### 标准答案",
                "",
                _markdown_block(str(sample["reference_answer"])),
                "",
                "#### 生成答案",
                "",
                _markdown_block(str(sample["answer"])),
                "",
                "#### Top-3证据",
                "",
            ]
        )
        for index, context in enumerate(sample["contexts"], 1):
            document = sample["documents"][index - 1] if index <= len(sample["documents"]) else {}
            lines.extend(
                [
                    f"##### [{index}] {document.get('title') or '未知标题'}",
                    "",
                    f"- doc_id：`{document.get('doc_id') or ''}`",
                    f"- source：{document.get('source') or ''}",
                    "",
                    _markdown_block(str(context)),
                    "",
                ]
            )
    lines.extend(
        [
            "## 人工确认清单",
            "",
            "- [ ] 重点样本的答案正确性已检查",
            "- [ ] 答案陈述均能映射到对应Context",
            "- [ ] 引用编号没有错位",
            "- [ ] 已知漏召回和合理拒答已单独记录",
            "- [ ] 同意以当前不可变输入运行完整RAGAS",
            "",
        ]
    )
    return "\n".join(lines)


def write_review_artifacts(
    rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    expected_questions: int,
    spotcheck_target: int,
) -> dict[str, Any]:
    summary, spotcheck = build_review(
        rows,
        expected_questions=expected_questions,
        spotcheck_target=spotcheck_target,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / SUMMARY_FILENAME
    spotcheck_json_path = output_dir / SPOTCHECK_JSON_FILENAME
    spotcheck_markdown_path = output_dir / SPOTCHECK_MARKDOWN_FILENAME
    _atomic_json(summary_path, summary)
    _atomic_json(spotcheck_json_path, spotcheck)
    _atomic_text(
        spotcheck_markdown_path,
        render_spotcheck_markdown(summary, spotcheck),
    )
    manifest = {
        "format_version": FORMAT_VERSION,
        "dataset": "watsonxDocsQA",
        "input_sha256": summary["input_sha256"],
        "samples": len(rows),
        "selected_question_ids": summary["spotcheck"]["question_ids"],
        "artifacts_sha256": {
            SUMMARY_FILENAME: sha256_file(summary_path),
            SPOTCHECK_JSON_FILENAME: sha256_file(spotcheck_json_path),
            SPOTCHECK_MARKDOWN_FILENAME: sha256_file(spotcheck_markdown_path),
        },
    }
    _atomic_json(output_dir / MANIFEST_FILENAME, manifest)
    approval_path = output_dir / APPROVAL_FILENAME
    if approval_path.exists():
        # 重新生成报告后旧确认必须显式重做；不删除文件，只标记为失效。
        try:
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            approval = {}
        if approval.get("manifest_sha256") != sha256_file(output_dir / MANIFEST_FILENAME):
            approval["valid"] = False
            approval["invalidated_at"] = datetime.now(UTC).isoformat()
            approval["invalidation_reason"] = "review_artifacts_regenerated"
            _atomic_json(approval_path, approval)
    return manifest


def approve_review(output_dir: Path, *, reviewer: str, note: str) -> dict[str, Any]:
    reviewer = reviewer.strip()
    if not reviewer:
        raise ReviewError("reviewer 不能为空")
    manifest_path = output_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ReviewError("review_manifest.json 不存在，请先生成抽查报告")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for filename, expected_sha in manifest.get("artifacts_sha256", {}).items():
        artifact_path = output_dir / filename
        if not artifact_path.is_file() or sha256_file(artifact_path) != expected_sha:
            raise ReviewError(f"抽查材料已变化或缺失：{filename}")
    approval = {
        "format_version": FORMAT_VERSION,
        "valid": True,
        "approved_at": datetime.now(UTC).isoformat(),
        "reviewer": reviewer,
        "note": note.strip(),
        "input_sha256": manifest["input_sha256"],
        "manifest_sha256": sha256_file(manifest_path),
    }
    _atomic_json(output_dir / APPROVAL_FILENAME, approval)
    return approval


def validate_approval(
    generations: Path,
    review_dir: Path,
    *,
    expected_questions: int,
) -> dict[str, Any]:
    rows = load_generation_rows(generations, expected_questions=expected_questions)
    current_input_sha = selected_input_sha256(rows)
    manifest_path = review_dir / MANIFEST_FILENAME
    approval_path = review_dir / APPROVAL_FILENAME
    if not manifest_path.is_file() or not approval_path.is_file():
        raise ReviewError("完整RAGAS前必须存在抽查manifest和人工approval")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if not approval.get("valid"):
        raise ReviewError("人工确认已失效，请重新审查并确认")
    if manifest.get("input_sha256") != current_input_sha:
        raise ReviewError("生成答案已变化，现有抽查报告失效")
    if approval.get("input_sha256") != current_input_sha:
        raise ReviewError("生成答案已变化，现有人工确认失效")
    if approval.get("manifest_sha256") != sha256_file(manifest_path):
        raise ReviewError("抽查manifest已变化，现有人工确认失效")
    for filename, expected_sha in manifest.get("artifacts_sha256", {}).items():
        path = review_dir / filename
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ReviewError(f"抽查材料已变化或缺失：{filename}")
    return approval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA 生成结果统计与人工抽查")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="生成统计与重点抽查报告")
    review.add_argument("--generations", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--expected-questions", type=int, default=30)
    review.add_argument("--spotcheck-target", type=int, default=10)

    approve = subparsers.add_parser("approve", help="人工检查后锁定当前报告")
    approve.add_argument("--output", type=Path, required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "review":
            rows = load_generation_rows(
                args.generations.resolve(),
                expected_questions=args.expected_questions,
            )
            manifest = write_review_artifacts(
                rows,
                args.output.resolve(),
                expected_questions=args.expected_questions,
                spotcheck_target=args.spotcheck_target,
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        else:
            approval = approve_review(
                args.output.resolve(),
                reviewer=args.reviewer,
                note=args.note,
            )
            print(json.dumps(approval, ensure_ascii=False, indent=2))
    except ReviewError as error:
        raise SystemExit(f"[FAIL] {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
