#!/usr/bin/env python3
"""合同审查本地安全 Smoke Test。

该检查不访问服务器、不读取真实合同，只验证脱敏规则与公开 API Schema 的边界。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.contract_review import ContractReviewDetail
from app.services.privacy_redaction import desensitize_text


def run_checks() -> dict[str, object]:
    original = "身份证号：110105199001011234，联系电话：13912345678，银行卡号：6222021234567890"
    redacted = desensitize_text(original).text
    checks = {
        "id_card_redacted": "110105199001011234" not in redacted,
        "phone_redacted": "13912345678" not in redacted,
        "bank_card_redacted": "6222021234567890" not in redacted,
        "raw_storage_path_not_in_public_schema": "storage_path"
        not in ContractReviewDetail.model_fields,
        "raw_contract_field_not_in_public_schema": "raw_text" not in ContractReviewDetail.model_fields,
    }
    return {
        "format_version": 1,
        "status": "passed" if all(checks.values()) else "failed",
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "redacted_sample": re.sub(r"\s+", " ", redacted),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="合同审查隐私边界 Smoke Test")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_checks()
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
