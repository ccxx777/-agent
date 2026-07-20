"""比较两个watsonxDocsQA检索基线，并以Hit@3不退化作为迁移门。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class ComparisonError(RuntimeError):
    """基线文件无效或v2没有通过切换门。"""


def load_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ComparisonError(f"summary不存在：{path}")
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComparisonError(f"summary无法读取：{path}") from error
    if int(summary.get("completed_questions") or 0) != int(
        summary.get("total_questions") or 0
    ):
        raise ComparisonError(f"基线未完整完成：{path}")
    if int(summary.get("error_questions") or 0) != 0:
        raise ComparisonError(f"基线包含错误题：{path}")
    return summary


def compare(old: dict[str, Any], new: dict[str, Any], *, tolerance: float) -> dict[str, Any]:
    if old.get("total_questions") != new.get("total_questions"):
        raise ComparisonError("两份基线题数不一致")
    metric_names = ("hit_at_1", "hit_at_3", "mrr_at_3", "mean_recall_at_3")
    deltas = {
        name: round(
            float(new["metrics"][name]) - float(old["metrics"][name]),
            6,
        )
        for name in metric_names
    }
    old_latency = float(old.get("latency_seconds", {}).get("mean") or 0.0)
    new_latency = float(new.get("latency_seconds", {}).get("mean") or 0.0)
    hit3_passed = deltas["hit_at_3"] + tolerance >= 0
    report = {
        "status": "passed" if hit3_passed else "failed",
        "gate": "new Hit@3 must not be lower than old Hit@3",
        "tolerance": tolerance,
        "old_collection": old.get("collection"),
        "new_collection": new.get("collection"),
        "old_metrics": old["metrics"],
        "new_metrics": new["metrics"],
        "metric_deltas": deltas,
        "mean_latency_seconds": {
            "old": old_latency,
            "new": new_latency,
            "delta": round(new_latency - old_latency, 6),
            "speedup": round(old_latency / new_latency, 3) if new_latency > 0 else None,
        },
        "old_zero_hit_question_ids": old.get("zero_hit_question_ids", []),
        "new_zero_hit_question_ids": new.get("zero_hit_question_ids", []),
    }
    if not hit3_passed:
        raise ComparisonError(json.dumps(report, ensure_ascii=False))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA v1/v2检索基线比较")
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.tolerance < 0:
        raise SystemExit("[FAIL] tolerance不能小于0")
    try:
        report = compare(
            load_summary(args.old.resolve()),
            load_summary(args.new.resolve()),
            tolerance=args.tolerance,
        )
    except ComparisonError as error:
        raise SystemExit(f"[FAIL] {error}") from error
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(output)
    print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
