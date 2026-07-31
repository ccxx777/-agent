"""合同事实的本地规范化与一致性检查。

这里不判断条款是否合法，只做三件事：清理模型字段、检查证据是否存在、发现同名事实的冲突。
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from app.schemas.contract_extraction import (
    LABOR_REQUIRED_FACT_FIELDS,
    LABOR_REQUIRED_FACT_KEYS,
    ContractEvidence,
    ContractFact,
    ContractFactDraft,
    FactStatus,
)

_STATUS_VALUES = {item.value for item in FactStatus}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalise_value(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = _clean_text(value)
        # 统一常见日期分隔符，方便后续规则引擎做精确比较；不做法律含义推断。
        cleaned = re.sub(r"(\d{4})年(\d{1,2})月(\d{1,2})日", r"\1-\2-\3", cleaned)
        cleaned = re.sub(r"(\d{4})年(\d{1,2})月", r"\1-\2", cleaned)
        return cleaned
    if isinstance(value, list):
        return [_normalise_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalise_value(item) for key, item in value.items()}
    return value


def _status(
    value: str | FactStatus,
    *,
    evidence: list[ContractEvidence],
    confidence: float,
    raw_value: Any,
) -> FactStatus:
    normalized = getattr(value, "value", value).strip().lower()
    if normalized not in _STATUS_VALUES:
        normalized = FactStatus.CONFIRMED.value
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return FactStatus.MISSING
    if not evidence or confidence < 0.65:
        return FactStatus.NEEDS_CONFIRMATION
    return FactStatus(normalized)


class ContractFactNormalizer:
    """把模型中间结果转换为可供 API/规则引擎使用的事实。"""

    def build_fact(
        self,
        draft: ContractFactDraft,
        *,
        fact_id: str,
        evidence: list[ContractEvidence],
    ) -> ContractFact:
        confidence = max(0.0, min(1.0, float(draft.confidence)))
        status = _status(
            draft.status,
            evidence=evidence,
            confidence=confidence,
            raw_value=draft.value,
        )
        needs_confirmation = bool(
            draft.needs_confirmation
            or status
            in {
                FactStatus.AMBIGUOUS,
                FactStatus.MISSING,
                FactStatus.CONTRADICTED,
                FactStatus.NEEDS_CONFIRMATION,
            }
        )
        return ContractFact(
            fact_id=fact_id,
            field_key=_clean_text(draft.field_key).lower(),
            category=_clean_text(draft.category or "other"),
            name=_clean_text(draft.name),
            value=draft.value,
            normalized_value=_normalise_value(draft.value),
            status=status,
            confidence=confidence,
            evidence=evidence,
            source_clause_ids=list(dict.fromkeys(draft.clause_ids)),
            needs_confirmation=needs_confirmation,
            note=_clean_text(draft.note) if draft.note else None,
        )

    def ensure_required_fields(
        self,
        facts: Iterable[ContractFact],
    ) -> tuple[list[ContractFact], list[str]]:
        """为没有被模型返回的劳动合同必备字段生成 ``missing`` 兜底事实。

        模型可以漏掉字段，但系统不能因此把“未提取”误认为“合同没有问题”。
        兜底事实没有合同证据和有效值，必须经过用户确认或补充后才能进入
        后续规则引擎。
        """

        result = list(facts)
        present = {
            fact.field_key
            for fact in result
            if fact.field_key in LABOR_REQUIRED_FACT_KEYS
        }
        missing: list[str] = []
        next_id = len(result) + 1
        for spec in LABOR_REQUIRED_FACT_FIELDS:
            if spec.field_key in present:
                continue
            missing.append(spec.field_key)
            result.append(
                ContractFact(
                    fact_id=f"fact_{next_id:03d}",
                    field_key=spec.field_key,
                    category=spec.category,
                    name=spec.name,
                    value=None,
                    normalized_value=None,
                    status=FactStatus.MISSING,
                    confidence=0.0,
                    evidence=[],
                    source_clause_ids=[],
                    needs_confirmation=True,
                    note="模型未返回该必备字段；需要确认合同是否包含相关内容。",
                )
            )
            next_id += 1
        return result, missing

    def mark_contradictions(self, facts: Iterable[ContractFact]) -> list[ContractFact]:
        grouped: dict[tuple[str, str], list[ContractFact]] = defaultdict(list)
        result = list(facts)
        for fact in result:
            grouped[(fact.category.lower(), fact.name.lower())].append(fact)
        for group in grouped.values():
            values = {
                repr(fact.normalized_value)
                for fact in group
                if fact.status is not FactStatus.MISSING and fact.normalized_value not in (None, "")
            }
            if len(values) <= 1:
                continue
            for fact in group:
                fact.status = FactStatus.CONTRADICTED
                fact.needs_confirmation = True
        return result

    def confirmation_questions(self, facts: Iterable[ContractFact]) -> list[str]:
        questions: list[str] = []
        for fact in facts:
            if not fact.needs_confirmation:
                continue
            question = self.confirmation_question(fact)
            if question not in questions:
                questions.append(question)
        return questions

    @staticmethod
    def confirmation_question(fact: ContractFact) -> str:
        """为单条事实生成确认问题，供列表和 question_items 共用。"""

        if fact.status is FactStatus.MISSING:
            return f"请补充或确认合同中的“{fact.name}”信息。"
        if fact.status is FactStatus.CONTRADICTED:
            return f"合同中“{fact.name}”出现不一致，请确认以哪一处内容为准。"
        if not fact.evidence:
            return f"请确认“{fact.name}”的原文位置和具体内容。"
        return f"请确认合同中的“{fact.name}”是否准确。"
