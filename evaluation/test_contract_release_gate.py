from __future__ import annotations

from evaluation.contract_release_gate import evaluate


def test_release_gate_blocks_unreviewed_expert_set() -> None:
    result = evaluate(
        legal_smoke={"status": "passed", "failed_queries": 0},
        expert_rows=(False, 15, 0),
        security_smoke={"status": "passed"},
        minimum_expert_questions=15,
    )

    assert result["status"] == "blocked"
    assert result["checks"]["expert_set_approved"] is False


def test_release_gate_passes_only_when_all_conditions_are_met() -> None:
    result = evaluate(
        legal_smoke={"status": "passed", "failed_queries": 0},
        expert_rows=(True, 15, 15),
        security_smoke={"status": "passed"},
        minimum_expert_questions=15,
    )

    assert result["status"] == "passed"
