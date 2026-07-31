"""合同条款切分、结构化事实提取和证据定位编排。

本服务只接收上传流程已经脱敏的页文本。LLM 输出被视为候选事实，随后必须经过
EvidenceLocator 和 ContractFactNormalizer 的本地校验；没有本地证据的事实不会被当作已确认事实。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.schemas.contract_extraction import (
    LABOR_REQUIRED_FACT_FIELDS,
    ContractClause,
    ContractExtractionResult,
    ContractFactDraft,
    ContractFactExtractionPayload,
    ExtractionStatus,
    FactStatus,
)
from app.services.contract_clause_extractor import (
    ContractClauseSplitter,
    EvidenceLocator,
)
from app.services.contract_fact_normalizer import ContractFactNormalizer

logger = logging.getLogger(__name__)

_FACT_REQUIRED_KEYS = frozenset(
    {
        "field_key",
        "category",
        "name",
        "value",
        "status",
        "confidence",
        "clause_ids",
        "evidence_quotes",
        "needs_confirmation",
        "note",
    }
)

_FACT_STATUS_VALUES = ", ".join(item.value for item in FactStatus)


def _required_field_prompt() -> str:
    return "\n".join(
        f"- {field.field_key}: {field.category} / {field.name}"
        for field in LABOR_REQUIRED_FACT_FIELDS
    )


class ContractExtractionRepository(Protocol):
    async def mark_extraction_status(
        self,
        review_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None: ...

    async def save_extraction(
        self,
        review_id: str,
        *,
        status: str,
        result: dict[str, Any],
    ) -> None: ...


class ContractExtractionError(RuntimeError):
    """事实提取失败，但不表示合同文件本身无效。"""


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ContractExtractionError("事实提取模型未返回有效 JSON")
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as error:
            raise ContractExtractionError("事实提取模型返回的 JSON 无法解析") from error
    if not isinstance(payload, dict):
        raise ContractExtractionError("事实提取模型返回格式不是 JSON 对象")
    return payload


class StructuredContractFactExtractor:
    """使用 OpenAI-compatible LangChain ChatModel 提取事实候选。"""

    def __init__(
        self,
        chat_model: Any,
        *,
        model_name: str,
        max_chars: int = 12000,
    ) -> None:
        self.chat_model = chat_model
        self.model_name = model_name
        self.max_chars = max_chars
        self.call_count = 0
        self.invalid_fact_count = 0

    async def extract(
        self,
        clauses: list[ContractClause],
        *,
        require_all_fields: bool = True,
    ) -> list[ContractFactDraft]:
        if not clauses:
            return []
        context_parts: list[str] = []
        remaining = self.max_chars
        for clause in clauses:
            text = clause.text[:remaining]
            if not text:
                break
            context_parts.append(
                f"CLAUSE_ID: {clause.clause_id}\n"
                f"CLAUSE_TYPE: {clause.clause_type.value}\n"
                f"TITLE: {clause.title}\n"
                f"TEXT:\n{text}"
            )
            remaining -= len(text)
            if remaining <= 0:
                break
        if not context_parts:
            return []

        coverage_instruction = (
            "必须覆盖下面所有劳动合同必备字段。即使合同中没有写，也必须输出一条\n"
            'value=null、status="missing"、needs_confirmation=true 的 fact，不能省略：'
            if require_all_fields
            else (
                "当前是长文档的一个批次，只返回本批次文本中有证据的事实；不要因为某个必备字段"
                "不在本批次就生成 missing。所有批次合并后，服务会在本地做必备字段覆盖检查并补齐"
                " missing 事实。"
            )
        )
        system_prompt = f"""
你是劳动合同事实抽取器，不是律师，也不做合法性、风险等级或是否签署的判断。
你只能根据给定的脱敏合同条款提取事实，绝对不能补充合同中没有写明的内容。

输出必须是一个 JSON 对象，不能输出 Markdown、解释文字或 JSON 之外的内容。
外层结构必须是：
{{
  "schema_version": 1,
  "facts": [
    {{
      "field_key": "probation_period",
      "category": "期限",
      "name": "试用期",
      "value": "6个月",
      "status": "confirmed",
      "confidence": 0.98,
      "clause_ids": ["clause_004"],
      "evidence_quotes": ["试用期为6个月"],
      "needs_confirmation": false,
      "note": null
    }},
    {{
      "field_key": "housing_fund",
      "category": "社会保险",
      "name": "住房公积金",
      "value": null,
      "status": "missing",
      "confidence": 0.0,
      "clause_ids": [],
      "evidence_quotes": [],
      "needs_confirmation": true,
      "note": "给定文本未找到住房公积金条款"
    }}
  ]
}}

每条 fact 必须同时包含以下字段，字段不能省略：
field_key、category、name、value、status、confidence、clause_ids、
evidence_quotes、needs_confirmation、note。

status 只能使用：{_FACT_STATUS_VALUES}。
value 可以是字符串、数字、数组、对象或 null。
confidence 必须是 0 到 1 之间的数字。
clause_ids 必须引用输入中真实存在的 CLAUSE_ID；找不到时使用空数组。
evidence_quotes 必须逐字摘录输入文本；找不到原文时使用空数组。

{coverage_instruction}
{_required_field_prompt()}

同一个 field_key 在合同中出现多个不同版本时，分别输出多条 fact，不要自行选择；
后续程序会做冲突检测。不要输出任何法律结论。
""".strip()
        user_prompt = (
            "请按照 System Prompt 中的 JSON 结构，完整提取下面这些脱敏劳动合同条款。"
            + (
                "对于输入文本没有出现的必备字段，必须返回 missing 事实，不得省略。"
                if require_all_fields
                else "当前只处理本批次有证据的字段，批次外的缺失字段由本地合并步骤补齐。"
            )
            + "\n\n"
            + "\n\n---\n\n".join(context_parts)
        )

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            self.call_count += 1
            response = await self.chat_model.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
        except Exception as error:
            raise ContractExtractionError("事实提取模型调用失败") from error

        payload = _parse_json_object(_response_text(response))
        try:
            envelope = ContractFactExtractionPayload.model_validate(payload)
        except ValidationError as error:
            raise ContractExtractionError(
                "事实提取 JSON 必须包含 schema_version=1 和 facts 数组"
            ) from error
        raw_facts = envelope.facts
        facts: list[ContractFactDraft] = []
        for item in raw_facts:
            if not isinstance(item, dict):
                self.invalid_fact_count += 1
                continue
            missing_keys = _FACT_REQUIRED_KEYS - set(item)
            if missing_keys:
                self.invalid_fact_count += 1
                logger.warning(
                    "忽略一条缺少字段的合同事实：missing=%s",
                    ",".join(sorted(missing_keys)),
                )
                continue
            try:
                facts.append(ContractFactDraft.model_validate(item))
            except ValidationError:
                self.invalid_fact_count += 1
                logger.warning("忽略一条格式不完整的合同事实")
        return facts


class ContractExtractionService:
    """合同事实提取主服务。"""

    def __init__(
        self,
        repository: ContractExtractionRepository,
        *,
        chat_model: Any | None = None,
        model_name: str | None = None,
        batch_clauses: int = 6,
        max_model_chars: int = 12000,
        single_pass_max_chars: int = 12000,
    ) -> None:
        self.repository = repository
        self.splitter = ContractClauseSplitter()
        self.locator = EvidenceLocator()
        self.normalizer = ContractFactNormalizer()
        self.model_name = model_name
        self.extractor = (
            StructuredContractFactExtractor(
                chat_model,
                model_name=model_name or "unknown",
                max_chars=max_model_chars,
            )
            if chat_model is not None
            else None
        )
        self.batch_clauses = max(1, batch_clauses)
        self.single_pass_max_chars = max(1, min(single_pass_max_chars, max_model_chars))

    async def process(self, review_id: str, pages: list[dict[str, Any]]) -> ContractExtractionResult:
        """处理一份已脱敏的合同页文本。"""

        await self.repository.mark_extraction_status(review_id, ExtractionStatus.RUNNING.value)
        try:
            clauses = self.splitter.split(pages)
            warnings: list[str] = []
            if not clauses:
                warnings.append("没有可用于条款切分的脱敏文本")

            if self.extractor is None:
                if not clauses:
                    warnings.append("请确认合同文本是否完整且可读取")
                result = ContractExtractionResult(
                    extraction_status=ExtractionStatus.NEEDS_CONFIRMATION,
                    clauses=clauses,
                    warnings=[*warnings, "未配置合同事实提取模型，暂未执行自动抽取"],
                    model=self.model_name,
                    extracted_at=datetime.now(UTC),
                )
                await self.repository.save_extraction(
                    review_id,
                    status=result.extraction_status.value,
                    result=result.model_dump(mode="json"),
                )
                return result

            clause_map = {clause.clause_id: clause for clause in clauses}
            total_chars = sum(len(clause.text) for clause in clauses)
            if total_chars <= self.single_pass_max_chars:
                batches = [clauses]
                extraction_mode = "single"
            else:
                batches = [
                    clauses[index : index + self.batch_clauses]
                    for index in range(0, len(clauses), self.batch_clauses)
                ]
                extraction_mode = "batch"
            logger.info(
                "Contract fact extraction plan: review_id=%s clauses=%s chars=%s mode=%s batches=%s",
                review_id,
                len(clauses),
                total_chars,
                extraction_mode,
                len(batches),
            )
            facts = []
            for batch in batches:
                drafts = await self.extractor.extract(
                    batch,
                    require_all_fields=extraction_mode == "single",
                )
                for draft in drafts:
                    evidence = self.locator.locate_fact(
                        evidence_quotes=draft.evidence_quotes,
                        value=draft.value,
                        pages=pages,
                        clauses=clause_map,
                        clause_ids=draft.clause_ids,
                    )
                    facts.append(
                        self.normalizer.build_fact(
                            draft,
                            fact_id=f"fact_{len(facts) + 1:03d}",
                            evidence=evidence,
                        )
                    )

            facts = self.normalizer.mark_contradictions(facts)
            facts, missing_required_fields = self.normalizer.ensure_required_fields(facts)
            questions = self.normalizer.confirmation_questions(facts)
            question_items = []
            for fact in facts:
                if not fact.needs_confirmation:
                    continue
                question = self.normalizer.confirmation_question(fact)
                reason = "low_confidence"
                if fact.status is FactStatus.MISSING:
                    reason = "missing"
                elif fact.status is FactStatus.CONTRADICTED:
                    reason = "contradicted"
                elif fact.status is FactStatus.AMBIGUOUS:
                    reason = "ambiguous"
                elif not fact.evidence:
                    reason = "no_evidence"
                question_items.append(
                    {
                        "question_id": f"question:{fact.fact_id}",
                        "fact_id": fact.fact_id,
                        "reason": reason,
                        "question_text": question,
                        "input_type": "text",
                        "required": True,
                    }
                )
            if not clauses and "请确认合同文本是否完整且可读取。" not in questions:
                questions.append("请确认合同文本是否完整且可读取。")
            elif clauses and not facts and "请确认合同中是否包含可识别的劳动合同事实。" not in questions:
                questions.append("请确认合同中是否包含可识别的劳动合同事实。")
            status = ExtractionStatus.NEEDS_CONFIRMATION if questions else ExtractionStatus.READY
            result = ContractExtractionResult(
                extraction_status=status,
                extraction_mode=extraction_mode,
                model_calls=self.extractor.call_count,
                invalid_fact_count=self.extractor.invalid_fact_count,
                clauses=clauses,
                facts=facts,
                confirmation_questions=questions,
                confirmation_question_items=question_items,
                warnings=[
                    *warnings,
                    *(
                        [
                            f"模型返回 {self.extractor.invalid_fact_count} 条格式不完整事实，已忽略。"
                        ]
                        if self.extractor.invalid_fact_count
                        else []
                    ),
                ],
                missing_required_fields=missing_required_fields,
                model=self.model_name,
                extracted_at=datetime.now(UTC),
            )
            await self.repository.save_extraction(
                review_id,
                status=status.value,
                result=result.model_dump(mode="json"),
            )
            return result
        except ContractExtractionError as error:
            await self.repository.mark_extraction_status(review_id, ExtractionStatus.FAILED.value)
            logger.warning("Contract fact extraction failed: review_id=%s error=%s", review_id, error)
            raise
        except Exception as error:
            await self.repository.mark_extraction_status(review_id, ExtractionStatus.FAILED.value)
            logger.exception("Unexpected contract fact extraction failure: review_id=%s", review_id)
            raise ContractExtractionError("合同事实提取服务暂时不可用") from error

    async def resume_pending(self) -> None:
        """进程重启后恢复处于 running 的事实提取任务。"""

        list_pending = getattr(self.repository, "list_pending_extractions", None)
        get_pages = getattr(self.repository, "get_pages", None)
        if list_pending is None or get_pages is None:
            return
        try:
            pending = await list_pending()
        except Exception:  # noqa: BLE001 - 迁移未执行时不阻塞服务启动
            logger.warning("Contract extraction recovery skipped; schema may not be migrated")
            return
        for record in pending:
            review_id = str(record["review_id"])
            pages = await get_pages(review_id)
            try:
                await self.process(review_id, pages)
            except ContractExtractionError:
                # 当前任务已被标记 failed，继续恢复其它任务。
                continue
