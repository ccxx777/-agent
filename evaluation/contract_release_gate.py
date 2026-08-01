#!/usr/bin/env python3
"""合同审查发布前门禁。

门禁把法律检索 Smoke、专家题集审批状态和安全 Smoke 作为三个独立条件，
避免只看一个 LLM 分数就把 staging 资料或未复核规则带入生产。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _expert_status(path: Path, minimum: int) -> tuple[bool, int, int]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved = sum(row.get("review_status") == "APPROVED" for row in rows)
    return len(rows) >= minimum and approved == len(rows), len(rows), approved


def evaluate(
    *,
    legal_smoke: dict[str, Any],
    expert_rows: tuple[bool, int, int],
    security_smoke: dict[str, Any],
    minimum_expert_questions: int,
) -> dict[str, Any]:
    expert_passed, expert_count, approved_count = expert_rows
    checks = {
        "legal_smoke_passed": legal_smoke.get("status") == "passed"
        and int(legal_smoke.get("failed_queries", 1)) == 0,
        "expert_set_approved": expert_passed,
        "security_smoke_passed": security_smoke.get("status") == "passed",
    }
    return {
        "format_version": 1,
        "status": "passed" if all(checks.values()) else "blocked",
        "checks": checks,
        "expert_questions": expert_count,
        "expert_approved": approved_count,
        "minimum_expert_questions": minimum_expert_questions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="合同审查发布门禁")
    parser.add_argument("--legal-smoke", type=Path, required=True)
    parser.add_argument("--expert-set", type=Path, required=True)
    parser.add_argument("--security-smoke", type=Path, required=True)
    parser.add_argument("--minimum-expert-questions", type=int, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(
        legal_smoke=_load(args.legal_smoke),
        expert_rows=_expert_status(args.expert_set, args.minimum_expert_questions),
        security_smoke=_load(args.security_smoke),
        minimum_expert_questions=args.minimum_expert_questions,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
