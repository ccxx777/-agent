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
    ContractClause,
    ContractExtractionResult,
    ContractFactDraft,
    ExtractionStatus,
    FactStatus,
)
from app.services.contract_clause_extractor import (
    ContractClauseSplitter,
    EvidenceLocator,
)
from app.services.contract_fact_normalizer import ContractFactNormalizer

logger = logging.getLogger(__name__)


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

    async def extract(self, clauses: list[ContractClause]) -> list[ContractFactDraft]:
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

        system_prompt = (
            "你是合同事实抽取器，不是律师，也不做合法性、风险等级或是否签署的判断。"
            "只根据给定的脱敏合同条款提取明确写出的事实。"
            "必须返回 JSON 对象：{\"facts\":[...]}。每条 fact 包含 category、name、value、"
            "status、confidence、clause_ids、evidence_quotes、needs_confirmation、note。"
            "evidence_quotes 必须逐字摘录给定文本；找不到原文时留空并将 needs_confirmation 设为 true。"
            "不要补充合同中没有出现的事实，不要输出法律结论。"
        )
        user_prompt = (
            "请提取劳动合同的当事人、期限、试用期、工作内容/地点、工资报酬、工时休假、"
            "社会保险、公积金、解除终止、违约责任、竞业限制、保密/知识产权和争议解决等事实。"
            "同一字段有多处不同表述时分别输出，后续程序会标记冲突。\n\n"
            + "\n\n---\n\n".join(context_parts)
        )

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await self.chat_model.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
        except Exception as error:
            raise ContractExtractionError("事实提取模型调用失败") from error

        payload = _parse_json_object(_response_text(response))
        raw_facts = payload.get("facts", [])
        if not isinstance(raw_facts, list):
            raise ContractExtractionError("事实提取 JSON 缺少 facts 数组")
        facts: list[ContractFactDraft] = []
        for item in raw_facts:
            if not isinstance(item, dict):
                continue
            try:
                facts.append(ContractFactDraft.model_validate(item))
            except ValidationError:
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
            facts = []
            for index in range(0, len(clauses), self.batch_clauses):
                batch = clauses[index : index + self.batch_clauses]
                drafts = await self.extractor.extract(batch)
                for draft_index, draft in enumerate(drafts, 1):
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
            questions = self.normalizer.confirmation_questions(facts)
            question_items = []
            uncertain_facts = [fact for fact in facts if fact.needs_confirmation]
            for fact, question in zip(uncertain_facts, questions, strict=False):
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
                clauses=clauses,
                facts=facts,
                confirmation_questions=questions,
                confirmation_question_items=question_items,
                warnings=warnings,
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
