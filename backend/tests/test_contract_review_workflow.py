from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.contract_review_workflow import (
    ContractReviewWorkflowService,
    build_contract_review_workflow,
)
from app.schemas.contract_extraction import (
    ClauseType,
    ContractClause,
    ContractExtractionResult,
    ExtractionStatus,
)
from app.schemas.retrieval import RetrievalPayload, RetrievedDocument


def _extraction() -> ContractExtractionResult:
    return ContractExtractionResult(
        extraction_status=ExtractionStatus.READY,
        clauses=[
            ContractClause(
                clause_id="clause-1",
                clause_type=ClauseType.SOCIAL_INSURANCE,
                title="社会保险",
                text="乙方自愿放弃社会保险",
                page_start=1,
                page_end=1,
            )
        ],
        facts=[],
    )


def _fact(*, ready: bool) -> SimpleNamespace:
    return SimpleNamespace(
        fact_id="fact-social",
        category="social_insurance",
        name="社会保险",
        original_value="乙方自愿放弃社会保险",
        effective_value="乙方自愿放弃社会保险" if ready else None,
        effective_source="contract" if ready else "none",
        confirmation_state="confirmed" if ready else "unreviewed",
        extraction_status="confirmed",
        evidence=[],
    )


class _Repository:
    async def get_task(self, review_id: str, user_id: str):
        return {
            "review_id": review_id,
            "user_id": user_id,
            "extraction_result": _extraction().model_dump(mode="json"),
        }


class _Confirmation:
    def __init__(self, *, ready: bool):
        self.ready_for_legal_review = ready
        self.facts = [_fact(ready=ready)]
        self.unresolved_questions = (
            [SimpleNamespace(question_text="请确认社会保险安排")]
            if not ready
            else []
        )


class _Retrieval:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    async def retrieve(self, query: str):
        self.calls.append(query)
        if self.fail:
            raise RuntimeError("collection unavailable")
        return RetrievalPayload(
            context="法律依据",
            contexts=["法律依据"],
            documents=[
                RetrievedDocument(
                    doc_id="law-1",
                    chunk_id="chunk-1",
                    title="劳动合同法",
                    source="official://law",
                    text="官方法律依据",
                    context_text="官方法律依据",
                    rank=1,
                )
            ],
        )


class ContractReviewWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_gate_stops_before_retrieval(self):
        confirmation = SimpleNamespace(
            get_confirmation=AsyncMock(return_value=_Confirmation(ready=False))
        )
        legal = _Retrieval()
        graph = build_contract_review_workflow(
            repository=_Repository(),
            confirmation_service=confirmation,
            legal_retrieval_service=legal,
            case_retrieval_service=None,
        )

        result = await ContractReviewWorkflowService(graph).run("review-1", "user-1")

        self.assertEqual(result.workflow_status.value, "awaiting_confirmation")
        self.assertIn("请确认社会保险安排", result.report.pending_questions)
        self.assertEqual(legal.calls, [])

    async def test_confirmed_facts_produce_sources_and_findings(self):
        confirmation = SimpleNamespace(
            get_confirmation=AsyncMock(return_value=_Confirmation(ready=True))
        )
        legal = _Retrieval()
        cases = _Retrieval()
        graph = build_contract_review_workflow(
            repository=_Repository(),
            confirmation_service=confirmation,
            legal_retrieval_service=legal,
            case_retrieval_service=cases,
        )

        result = await ContractReviewWorkflowService(graph).run("review-2", "user-1")

        self.assertEqual(result.workflow_status.value, "completed")
        self.assertTrue(any(item.rule_id == "LC-010" for item in result.report.findings))
        self.assertTrue(result.report.legal_sources)
        self.assertTrue(result.report.case_sources)
        self.assertEqual(result.report.legal_sources[0].source_level, "A")

    async def test_legal_retrieval_failure_is_partial_not_no_risk(self):
        confirmation = SimpleNamespace(
            get_confirmation=AsyncMock(return_value=_Confirmation(ready=True))
        )
        graph = build_contract_review_workflow(
            repository=_Repository(),
            confirmation_service=confirmation,
            legal_retrieval_service=_Retrieval(fail=True),
            case_retrieval_service=None,
        )

        result = await ContractReviewWorkflowService(graph).run("review-3", "user-1")

        self.assertEqual(result.workflow_status.value, "partial")
        self.assertTrue(result.report.warnings)
        self.assertTrue(any(item.risk_level.value == "high" for item in result.report.findings))


if __name__ == "__main__":
    unittest.main()
