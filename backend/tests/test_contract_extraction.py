"""合同条款切分、事实规范化和证据定位测试。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.schemas.contract_extraction import (
    ContractFactExtractionPayload,
    ExtractionStatus,
    FactStatus,
)
from app.services.contract_clause_extractor import (
    ContractClauseSplitter,
    EvidenceLocator,
)
from app.services.contract_extraction_service import ContractExtractionService


def _fact(
    field_key: str,
    category: str,
    name: str,
    value,
    *,
    status: str = "confirmed",
    confidence: float = 0.95,
    clause_ids: list[str] | None = None,
    evidence_quotes: list[str] | None = None,
    needs_confirmation: bool = False,
    note: str | None = None,
) -> dict:
    """构造与生产 Prompt 相同的完整 fact，避免测试绕过 JSON 契约。"""

    return {
        "field_key": field_key,
        "category": category,
        "name": name,
        "value": value,
        "status": status,
        "confidence": confidence,
        "clause_ids": clause_ids or [],
        "evidence_quotes": evidence_quotes or [],
        "needs_confirmation": needs_confirmation,
        "note": note,
    }


class _FakeModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
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
    def test_extraction_payload_schema_is_strict_at_outer_level(self):
        payload = ContractFactExtractionPayload.model_validate(
            {"schema_version": 1, "facts": []}
        )
        self.assertEqual(payload.schema_version, 1)
        with self.assertRaises(ValueError):
            ContractFactExtractionPayload.model_validate(
                {"schema_version": 2, "facts": []}
            )
        with self.assertRaises(ValueError):
            ContractFactExtractionPayload.model_validate(
                {"schema_version": 1, "facts": [], "extra": True}
            )

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

    async def test_short_document_uses_single_call_and_missing_field_fallback(self):
        repository = _FakeRepository()
        model = _FakeModel(
            {
                "schema_version": 1,
                "facts": [
                    _fact(
                        "salary",
                        "劳动报酬",
                        "月工资",
                        "8000元",
                        clause_ids=["clause_002"],
                        evidence_quotes=["月工资为8000元"],
                    )
                ],
            }
        )
        service = ContractExtractionService(
            repository,
            chat_model=model,
            model_name="fake-model",
        )
        pages = [
            {"page_no": 1, "text": "第一条 当事人\n甲方：某公司\n乙方：张三\n"},
            {"page_no": 2, "text": "第二条 劳动报酬\n月工资为8000元。\n"},
        ]

        result = await service.process("review-short", pages)

        self.assertEqual(result.extraction_mode, "single")
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(len(model.calls), 1)
        system_prompt = model.calls[0][0].content
        self.assertIn('"schema_version": 1', system_prompt)
        self.assertIn("每条 fact 必须同时包含以下字段", system_prompt)
        self.assertIn("value=null、status=\"missing\"", system_prompt)
        self.assertIn("contract_term", result.missing_required_fields)
        self.assertEqual(result.extraction_status, ExtractionStatus.NEEDS_CONFIRMATION)
        self.assertTrue(any(f.field_key == "salary" for f in result.facts))
        self.assertTrue(
            any(
                f.field_key == "contract_term" and f.status is FactStatus.MISSING
                for f in result.facts
            )
        )

    async def test_long_document_is_split_into_batches(self):
        repository = _FakeRepository()
        model = _FakeModel({"schema_version": 1, "facts": []})
        service = ContractExtractionService(
            repository,
            chat_model=model,
            model_name="fake-model",
            batch_clauses=2,
            max_model_chars=1000,
            single_pass_max_chars=10,
        )
        pages = [
            {
                "page_no": 1,
                "text": (
                    "第一条 当事人\n甲方：某公司\n乙方：张三\n"
                    "第二条 劳动报酬\n月工资为8000元。\n"
                    "第三条 工作内容\n从事数据分析工作。\n"
                    "第四条 工作地点\n工作地点为武汉。\n"
                    "第五条 工时\n每日工作八小时。\n"
                ),
            }
        ]

        result = await service.process("review-long", pages)

        self.assertEqual(result.extraction_mode, "batch")
        self.assertEqual(result.model_calls, 3)
        self.assertEqual(len(model.calls), 3)
        self.assertIn("当前是长文档的一个批次", model.calls[0][0].content)
        self.assertTrue(result.missing_required_fields)

    async def test_invalid_fact_is_skipped_and_counted(self):
        repository = _FakeRepository()
        model = _FakeModel(
            {
                "schema_version": 1,
                "facts": [
                    {"field_key": "salary", "name": "月工资"},
                    _fact(
                        "contract_term",
                        "期限",
                        "劳动合同期限",
                        "三年",
                        clause_ids=[],
                        evidence_quotes=[],
                        needs_confirmation=True,
                    ),
                ],
            }
        )
        service = ContractExtractionService(
            repository,
            chat_model=model,
            model_name="fake-model",
        )

        result = await service.process(
            "review-invalid",
            [{"page_no": 1, "text": "第一条 合同期限\n合同期限为三年。\n"}],
        )

        self.assertEqual(result.invalid_fact_count, 1)
        self.assertTrue(any("格式不完整" in warning for warning in result.warnings))
        self.assertTrue(
            any(f.field_key == "salary" and f.status is FactStatus.MISSING for f in result.facts)
        )
        self.assertEqual(result.extraction_status, ExtractionStatus.NEEDS_CONFIRMATION)


if __name__ == "__main__":
    unittest.main()
