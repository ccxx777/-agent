"""合同事实确认层的状态、证据和并发控制测试。"""

from __future__ import annotations

import copy
import unittest

from app.infrastructure.contract_review_repository import ConfirmationRevisionConflict
from app.schemas.contract_confirmation import (
    ConfirmationAction,
    ConfirmationStatus,
    FactConfirmationItem,
    FactConfirmationRequest,
)
from app.schemas.contract_extraction import (
    ContractClause,
    ContractEvidence,
    ContractExtractionResult,
    ContractFact,
    ExtractionStatus,
    FactStatus,
)
from app.services.contract_confirmation_service import (
    ContractConfirmationError,
    ContractFactConfirmationService,
)


class _MemoryRepository:
    def __init__(self, extraction: ContractExtractionResult) -> None:
        self.events: list[dict] = []
        self.record = {
            "review_id": "review-1",
            "user_id": "user-1",
            "extraction_result": extraction.model_dump(mode="json"),
            "pages": [
                {
                    "page_no": 1,
                    "text": "Salary: 8000 RMB\nThe term is not specified.",
                }
            ],
            "confirmation_status": "not_started",
            "confirmation_revision": 0,
            "confirmation_result": None,
        }

    async def get_task(self, review_id: str, user_id: str) -> dict | None:
        if review_id != self.record["review_id"] or user_id != self.record["user_id"]:
            return None
        return copy.deepcopy(self.record)

    async def get_confirmation_request(self, review_id: str, user_id: str, request_id: str) -> dict | None:
        if review_id != self.record["review_id"] or user_id != self.record["user_id"]:
            return None
        return next((event for event in self.events if event.get("request_id") == request_id), None)

    async def save_confirmation_state(
        self,
        review_id: str,
        user_id: str,
        *,
        expected_revision: int,
        status: str,
        snapshot: dict,
        events: list[dict],
        request_id: str | None,
    ) -> int:
        if expected_revision != self.record["confirmation_revision"]:
            raise ConfirmationRevisionConflict(review_id)
        self.record["confirmation_revision"] += 1
        self.record["confirmation_status"] = status
        self.record["confirmation_result"] = copy.deepcopy(snapshot)
        self.events.extend({**event, "request_id": request_id} for event in events)
        return self.record["confirmation_revision"]


def _extraction() -> ContractExtractionResult:
    return ContractExtractionResult(
        extraction_status=ExtractionStatus.NEEDS_CONFIRMATION,
        clauses=[
            ContractClause(
                clause_id="clause_001",
                title="Compensation",
                text="Salary: 8000 RMB",
                page_start=1,
                page_end=1,
                source_page_nos=[1],
            )
        ],
        facts=[
            ContractFact(
                fact_id="fact_salary",
                category="compensation",
                name="salary",
                value="8000 RMB",
                normalized_value="8000 RMB",
                status=FactStatus.CONFIRMED,
                confidence=0.98,
                evidence=[
                    ContractEvidence(
                        page_no=1,
                        quote="Salary: 8000 RMB",
                        char_start=0,
                        char_end=16,
                        clause_id="clause_001",
                    )
                ],
                source_clause_ids=["clause_001"],
            ),
            ContractFact(
                fact_id="fact_term",
                category="term",
                name="term",
                value=None,
                normalized_value=None,
                status=FactStatus.MISSING,
                confidence=0.0,
                evidence=[],
                needs_confirmation=True,
            ),
        ],
    )


class ContractConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = _MemoryRepository(_extraction())
        self.service = ContractFactConfirmationService(self.repository)

    async def test_initial_view_preserves_original_and_exposes_missing_question(self):
        result = await self.service.get_confirmation("review-1", "user-1")
        assert result is not None
        self.assertEqual(result.confirmation_status, ConfirmationStatus.NOT_STARTED)
        self.assertEqual(result.facts[0].original_value, "8000 RMB")
        self.assertEqual(result.facts[0].effective_value, "8000 RMB")
        self.assertEqual(result.facts[0].effective_source.value, "contract")
        self.assertEqual(result.unresolved_questions[0].fact_id, "fact_term")

    async def test_correct_requires_contract_evidence_and_supplement_uses_user_source(self):
        with self.assertRaises(ContractConfirmationError) as context:
            await self.service.apply_confirmation(
                "review-1",
                "user-1",
                FactConfirmationRequest(
                    base_revision=0,
                    items=[
                        FactConfirmationItem(
                            fact_id="fact_salary",
                            action=ConfirmationAction.CORRECT,
                            value="9000 RMB",
                        )
                    ],
                ),
            )
        self.assertEqual(context.exception.code, "correction_evidence_not_found")

        result = await self.service.apply_confirmation(
            "review-1",
            "user-1",
            FactConfirmationRequest(
                base_revision=0,
                submit=True,
                request_id="term-supplement-1",
                items=[
                    FactConfirmationItem(
                        fact_id="fact_term",
                        action=ConfirmationAction.SUPPLEMENT,
                        value="12 months",
                    )
                ],
            ),
        )
        assert result is not None
        self.assertEqual(result.confirmation_status, ConfirmationStatus.COMPLETED)
        term = next(fact for fact in result.facts if fact.fact_id == "fact_term")
        salary = next(fact for fact in result.facts if fact.fact_id == "fact_salary")
        self.assertEqual(term.effective_source.value, "user")
        self.assertEqual(term.effective_value, "12 months")
        self.assertEqual(salary.original_value, "8000 RMB")

    async def test_not_applicable_and_defer_are_distinct_states(self):
        result = await self.service.apply_confirmation(
            "review-1",
            "user-1",
            FactConfirmationRequest(
                base_revision=0,
                items=[
                    FactConfirmationItem(
                        fact_id="fact_term",
                        action=ConfirmationAction.NOT_APPLICABLE,
                        note="本合同不约定固定期限",
                    )
                ],
            ),
        )
        assert result is not None
        term = next(fact for fact in result.facts if fact.fact_id == "fact_term")
        self.assertEqual(term.confirmation_state.value, "not_applicable")
        self.assertEqual(term.effective_source.value, "none")
        self.assertFalse(result.unresolved_questions)

        result = await self.service.apply_confirmation(
            "review-1",
            "user-1",
            FactConfirmationRequest(
                base_revision=1,
                items=[FactConfirmationItem(fact_id="fact_term", action=ConfirmationAction.DEFER)],
            ),
        )
        assert result is not None
        self.assertEqual(next(fact for fact in result.facts if fact.fact_id == "fact_term").confirmation_state.value, "deferred")
        self.assertTrue(result.unresolved_questions)

    async def test_stale_revision_and_request_id_idempotency(self):
        first = await self.service.apply_confirmation(
            "review-1",
            "user-1",
            FactConfirmationRequest(
                base_revision=0,
                request_id="confirm-salary-1",
                items=[FactConfirmationItem(fact_id="fact_salary", action=ConfirmationAction.CONFIRM)],
            ),
        )
        assert first is not None
        self.assertEqual(first.confirmation_revision, 1)

        duplicate = await self.service.apply_confirmation(
            "review-1",
            "user-1",
            FactConfirmationRequest(
                base_revision=0,
                request_id="confirm-salary-1",
                items=[FactConfirmationItem(fact_id="fact_salary", action=ConfirmationAction.CONFIRM)],
            ),
        )
        assert duplicate is not None
        self.assertEqual(duplicate.confirmation_revision, 1)
        self.assertEqual(len(self.repository.events), 1)

        with self.assertRaises(ContractConfirmationError) as context:
            await self.service.apply_confirmation(
                "review-1",
                "user-1",
                FactConfirmationRequest(
                    base_revision=0,
                    items=[FactConfirmationItem(fact_id="fact_salary", action=ConfirmationAction.CONFIRM)],
                ),
            )
        self.assertEqual(context.exception.code, "stale_revision")


if __name__ == "__main__":
    unittest.main()
