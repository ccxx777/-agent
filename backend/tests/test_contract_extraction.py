"""合同条款切分、事实规范化和证据定位测试。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.schemas.contract_extraction import ExtractionStatus, FactStatus
from app.services.contract_clause_extractor import (
    ContractClauseSplitter,
    EvidenceLocator,
)
from app.services.contract_extraction_service import ContractExtractionService


class _FakeModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


class _FakeRepository:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.result: dict | None = None

    async def mark_extraction_status(self, _review_id: str, status: str, *, result=None) -> None:
        self.statuses.append(status)

    async def save_extraction(self, _review_id: str, *, status: str, result: dict) -> None:
        self.statuses.append(status)
        self.result = result


class ContractExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_clause_splitter_preserves_page_range_and_classifies_titles(self):
        clauses = ContractClauseSplitter().split(
            [
                {"page_no": 1, "text": "第一条 当事人\n甲方：某公司\n乙方：张三\n"},
                {"page_no": 2, "text": "第二条 劳动报酬\n月工资为8000元。\n"},
            ]
        )

        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0].clause_type.value, "parties")
        self.assertEqual(clauses[0].source_page_nos, [1])
        self.assertEqual(clauses[1].clause_type.value, "compensation")
        self.assertEqual(clauses[1].page_start, 2)

    def test_evidence_locator_returns_original_character_offsets(self):
        pages = [{"page_no": 1, "text": "劳动报酬\n月工资为8000元。"}]
        evidence = EvidenceLocator().locate_quote("劳动报酬 月工资为8000元。", pages)

        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.match_type.value, "normalized")
        self.assertEqual(pages[0]["text"][evidence.char_start : evidence.char_end], evidence.quote)

    async def test_extraction_requires_local_evidence_and_saves_result(self):
        repository = _FakeRepository()
        service = ContractExtractionService(
            repository,
            chat_model=_FakeModel(
                {
                    "facts": [
                        {
                            "category": "compensation",
                            "name": "月工资",
                            "value": "8000元",
                            "confidence": 0.95,
                            "clause_ids": ["clause_002"],
                            "evidence_quotes": ["月工资为8000元"],
                        },
                        {
                            "category": "term",
                            "name": "合同期限",
                            "value": "三年",
                            "confidence": 0.95,
                            "clause_ids": ["clause_002"],
                            "evidence_quotes": [],
                        },
                    ]
                }
            ),
            model_name="fake-model",
        )
        pages = [
            {
                "page_no": 1,
                "text": "第一条 当事人\n甲方：某公司\n乙方：张三\n",
            },
            {
                "page_no": 2,
                "text": "第二条 劳动报酬\n月工资为8000元。\n",
            },
        ]

        result = await service.process("review-1", pages)

        self.assertEqual(result.extraction_status, ExtractionStatus.NEEDS_CONFIRMATION)
        self.assertEqual(len(result.clauses), 2)
        self.assertEqual(result.facts[0].status, FactStatus.CONFIRMED)
        self.assertEqual(result.facts[0].evidence[0].page_no, 2)
        self.assertEqual(result.facts[1].status, FactStatus.NEEDS_CONFIRMATION)
        self.assertTrue(result.confirmation_questions)
        self.assertIsNotNone(repository.result)


if __name__ == "__main__":
    unittest.main()
