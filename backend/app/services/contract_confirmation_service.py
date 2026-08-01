"""合同事实确认服务。

该服务是一个确定性的状态层：它读取已经脱敏并定位过证据的提取结果，
接受用户的五类动作，生成有效事实快照并写入审计记录。它不调用 LLM，
也不做“是否合法/是否应该签署”的法律结论。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, ClassVar

from app.infrastructure.contract_review_repository import (
    ConfirmationRevisionConflict,
    ContractReviewRepository,
)
from app.schemas.contract_confirmation import (
    ConfirmationAction,
    ConfirmationQuestion,
    ConfirmationQuestionReason,
    ConfirmationState,
    ConfirmationStatus,
    ContractConfirmationResponse,
    EffectiveSource,
    FactConfirmationItem,
    FactConfirmationRequest,
    FactConfirmationView,
)
from app.schemas.contract_extraction import (
    ContractClause,
    ContractEvidence,
    ContractExtractionResult,
    ContractFact,
    FactStatus,
)
from app.services.contract_clause_extractor import EvidenceLocator


class ContractConfirmationError(RuntimeError):
    """确认层输入或状态不满足业务约束。"""

    def __init__(self, message: str, *, code: str = "invalid_confirmation") -> None:
        super().__init__(message)
        self.code = code


class ContractConfirmationNotReady(ContractConfirmationError):
    """事实提取还没有产生可确认结果。"""


class ContractFactConfirmationService:
    """管理合同事实的确认表单、状态快照和并发提交。"""

    _UNRESOLVED_STATUSES: ClassVar[set[FactStatus]] = {
        FactStatus.MISSING,
        FactStatus.AMBIGUOUS,
        FactStatus.CONTRADICTED,
        FactStatus.NEEDS_CONFIRMATION,
    }

    def __init__(
        self,
        repository: ContractReviewRepository,
        *,
        evidence_locator: EvidenceLocator | None = None,
    ) -> None:
        self.repository = repository
        self.evidence_locator = evidence_locator or EvidenceLocator()

    async def get_confirmation(
        self,
        review_id: str,
        user_id: str,
    ) -> ContractConfirmationResponse | None:
        record = await self.repository.get_task(review_id, user_id)
        if record is None:
            return None
        extraction = self._load_extraction(record)
        revision = int(record.get("confirmation_revision") or 0)
        stored = record.get("confirmation_result") or {}
        snapshot = self._merge_snapshot(extraction, stored)
        status = self._status_from_record(record, snapshot, extraction)
        return self._build_response(review_id, extraction, snapshot, status, revision)

    async def apply_confirmation(
        self,
        review_id: str,
        user_id: str,
        request: FactConfirmationRequest,
    ) -> ContractConfirmationResponse | None:
        record = await self.repository.get_task(review_id, user_id)
        if record is None:
            return None
        extraction = self._load_extraction(record)
        revision = int(record.get("confirmation_revision") or 0)
        stored = record.get("confirmation_result") or {}

        if request.request_id:
            duplicate = await self.repository.get_confirmation_request(
                review_id,
                user_id,
                request.request_id,
            )
            if duplicate is not None:
                latest = await self.repository.get_task(review_id, user_id)
                if latest is None:
                    return None
                latest_extraction = self._load_extraction(latest)
                latest_snapshot = self._merge_snapshot(
                    latest_extraction,
                    latest.get("confirmation_result") or {},
                )
                latest_status = self._status_from_record(latest, latest_snapshot, latest_extraction)
                return self._build_response(
                    review_id,
                    latest_extraction,
                    latest_snapshot,
                    latest_status,
                    int(latest.get("confirmation_revision") or 0),
                )

        if request.base_revision != revision:
            raise ContractConfirmationError(
                "确认表单版本已变化，请刷新后重新提交。",
                code="stale_revision",
            )
        if request.submit and not request.items:
            raise ContractConfirmationError(
                "提交确认时至少需要包含一条事实操作。",
                code="empty_submission",
            )

        snapshot = self._merge_snapshot(extraction, stored)
        facts_by_id = {fact.fact_id: fact for fact in extraction.facts}
        seen: set[str] = set()
        events: list[dict[str, Any]] = []
        for item in request.items:
            if item.fact_id in seen:
                raise ContractConfirmationError(
                    f"事实 {item.fact_id} 在同一请求中重复提交。",
                    code="duplicate_fact",
                )
            seen.add(item.fact_id)
            fact = facts_by_id.get(item.fact_id)
            if fact is None:
                raise ContractConfirmationError(
                    f"事实 {item.fact_id} 不存在或不属于当前合同。",
                    code="unknown_fact",
                )
            snapshot["facts"][item.fact_id] = self._apply_item(
                item,
                fact=fact,
                snapshot=snapshot,
                pages=list(record.get("pages") or []),
                clauses={clause.clause_id: clause for clause in extraction.clauses},
            )
            events.append(
                {
                    "fact_id": item.fact_id,
                    "action": item.action.value,
                    "user_value": item.value,
                    "note": item.note,
                }
            )

        unresolved = self._unresolved_questions(extraction, snapshot)
        if request.submit:
            status = (
                ConfirmationStatus.COMPLETED.value
                if not unresolved
                else ConfirmationStatus.PENDING.value
            )
        elif request.items:
            status = ConfirmationStatus.IN_PROGRESS.value
        else:
            status = str(record.get("confirmation_status") or ConfirmationStatus.NOT_STARTED.value)

        snapshot["version"] = 1
        snapshot["status"] = status
        snapshot["updated_by"] = user_id
        try:
            await self.repository.save_confirmation_state(
                review_id,
                user_id,
                expected_revision=revision,
                status=status,
                snapshot=snapshot,
                events=events,
                request_id=request.request_id,
            )
        except ConfirmationRevisionConflict as error:
            raise ContractConfirmationError(
                "确认表单版本已变化，请刷新后重新提交。",
                code="stale_revision",
            ) from error

        latest = await self.repository.get_task(review_id, user_id)
        if latest is None:
            return None
        latest_extraction = self._load_extraction(latest)
        latest_snapshot = self._merge_snapshot(
            latest_extraction,
            latest.get("confirmation_result") or {},
        )
        latest_status = self._status_from_record(latest, latest_snapshot, latest_extraction)
        return self._build_response(
            review_id,
            latest_extraction,
            latest_snapshot,
            latest_status,
            int(latest.get("confirmation_revision") or revision + 1),
        )

    def _load_extraction(self, record: Mapping[str, Any]) -> ContractExtractionResult:
        raw = record.get("extraction_result")
        if not raw:
            raise ContractConfirmationNotReady(
                "事实提取尚未完成，暂时不能确认。",
                code="extraction_not_ready",
            )
        try:
            return ContractExtractionResult.model_validate(raw)
        except ValueError as error:
            raise ContractConfirmationNotReady(
                "事实提取结果不可用，请重新运行提取。",
                code="invalid_extraction_result",
            ) from error

    def _merge_snapshot(
        self,
        extraction: ContractExtractionResult,
        stored: Mapping[str, Any],
    ) -> dict[str, Any]:
        stored_facts = stored.get("facts") if isinstance(stored, Mapping) else None
        stored_facts = stored_facts if isinstance(stored_facts, Mapping) else {}
        facts: dict[str, dict[str, Any]] = {}
        for fact in extraction.facts:
            initial = self._initial_fact_state(fact)
            override = stored_facts.get(fact.fact_id)
            if isinstance(override, Mapping):
                initial.update(deepcopy(dict(override)))
            facts[fact.fact_id] = initial
        return {"version": 1, "facts": facts}

    def _initial_fact_state(self, fact: ContractFact) -> dict[str, Any]:
        needs_confirmation = bool(fact.needs_confirmation or fact.status in self._UNRESOLVED_STATUSES)
        if needs_confirmation:
            return {
                "user_value": None,
                "effective_value": None,
                "effective_source": EffectiveSource.NONE.value,
                "confirmation_state": ConfirmationState.UNREVIEWED.value,
                "confirmation_note": None,
                "evidence": [item.model_dump(mode="json") for item in fact.evidence],
            }
        return {
            "user_value": None,
            "effective_value": fact.value,
            "effective_source": EffectiveSource.CONTRACT.value,
            "confirmation_state": ConfirmationState.CONFIRMED.value,
            "confirmation_note": None,
            "evidence": [item.model_dump(mode="json") for item in fact.evidence],
        }

    @staticmethod
    def _has_usable_value(value: Any) -> bool:
        """判断提取值是否足以让用户执行“确认原文”。"""

        if value is None:
            return False
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized not in {"", "未识别", "unidentified", "unknown"}
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    @classmethod
    def _allowed_actions(cls, fact: ContractFact) -> list[ConfirmationAction]:
        """根据证据条件返回可执行动作，避免前端展示必然失败的确认按钮。"""

        actions = [
            ConfirmationAction.SUPPLEMENT,
            ConfirmationAction.NOT_APPLICABLE,
            ConfirmationAction.DEFER,
        ]
        if fact.evidence:
            actions.insert(0, ConfirmationAction.CORRECT)
        if (
            fact.status is FactStatus.CONFIRMED
            and not fact.needs_confirmation
            and cls._has_usable_value(fact.value)
            and fact.evidence
        ):
            actions.insert(0, ConfirmationAction.CONFIRM)
        return actions

    def _apply_item(
        self,
        item: FactConfirmationItem,
        *,
        fact: ContractFact,
        snapshot: Mapping[str, Any],
        pages: list[dict[str, Any]],
        clauses: dict[str, ContractClause],
    ) -> dict[str, Any]:
        current = deepcopy(dict(snapshot["facts"].get(item.fact_id) or self._initial_fact_state(fact)))
        action = item.action

        if action is ConfirmationAction.CONFIRM:
            if (
                fact.status is not FactStatus.CONFIRMED
                or fact.needs_confirmation
                or not self._has_usable_value(fact.value)
                or not fact.evidence
            ):
                raise ContractConfirmationError(
                    f"事实“{fact.name}”没有足够合同证据，不能直接确认；请补充信息。",
                    code="confirm_without_evidence",
                )
            current.update(
                user_value=None,
                effective_value=fact.value,
                effective_source=EffectiveSource.CONTRACT.value,
                confirmation_state=ConfirmationState.CONFIRMED.value,
                evidence=[item.model_dump(mode="json") for item in fact.evidence],
            )
        elif action is ConfirmationAction.CORRECT:
            self._require_value(item, action)
            evidence = self.evidence_locator.locate_fact(
                evidence_quotes=[str(item.value)],
                value=item.value,
                pages=pages,
                clauses=clauses,
                clause_ids=fact.source_clause_ids,
            )
            if not evidence:
                raise ContractConfirmationError(
                    "修改值未在脱敏合同中找到证据，请改用“补充”。",
                    code="correction_evidence_not_found",
                )
            current.update(
                user_value=item.value,
                effective_value=item.value,
                effective_source=EffectiveSource.CONTRACT.value,
                confirmation_state=ConfirmationState.CORRECTED.value,
                evidence=[entry.model_dump(mode="json") for entry in evidence],
            )
        elif action is ConfirmationAction.SUPPLEMENT:
            self._require_value(item, action)
            current.update(
                user_value=item.value,
                effective_value=item.value,
                effective_source=EffectiveSource.USER.value,
                confirmation_state=ConfirmationState.SUPPLEMENTED.value,
            )
        elif action is ConfirmationAction.NOT_APPLICABLE:
            current.update(
                effective_value=None,
                effective_source=EffectiveSource.NONE.value,
                confirmation_state=ConfirmationState.NOT_APPLICABLE.value,
            )
        elif action is ConfirmationAction.DEFER:
            current.update(
                effective_value=None,
                effective_source=EffectiveSource.NONE.value,
                confirmation_state=ConfirmationState.DEFERRED.value,
            )
        current["confirmation_note"] = item.note
        return current

    @staticmethod
    def _require_value(item: FactConfirmationItem, action: ConfirmationAction) -> None:
        value = item.value
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ContractConfirmationError(
                f"{action.value} 操作必须提供非空 value。",
                code="value_required",
            )

    def _build_response(
        self,
        review_id: str,
        extraction: ContractExtractionResult,
        snapshot: Mapping[str, Any],
        status: str,
        revision: int,
    ) -> ContractConfirmationResponse:
        facts: list[FactConfirmationView] = []
        questions = self._unresolved_questions(extraction, snapshot)
        question_ids = {question.fact_id: [question.question_id] for question in questions}
        for fact in extraction.facts:
            state = snapshot["facts"].get(fact.fact_id) or self._initial_fact_state(fact)
            try:
                evidence = [ContractEvidence.model_validate(item) for item in state.get("evidence", [])]
            except ValueError:
                evidence = list(fact.evidence)
            source = EffectiveSource(state.get("effective_source", EffectiveSource.NONE.value))
            confirmation_state = ConfirmationState(
                state.get("confirmation_state", ConfirmationState.UNREVIEWED.value)
            )
            facts.append(
                FactConfirmationView(
                    fact_id=fact.fact_id,
                    field_key=fact.field_key,
                    category=fact.category,
                    name=fact.name,
                    original_value=fact.value,
                    normalized_original_value=fact.normalized_value,
                    user_value=state.get("user_value"),
                    effective_value=state.get("effective_value"),
                    effective_source=source,
                    confirmation_state=confirmation_state,
                    extraction_status=fact.status,
                    confidence=fact.confidence,
                    evidence=evidence,
                    source_clause_ids=fact.source_clause_ids,
                    question_ids=question_ids.get(fact.fact_id, []),
                    allowed_actions=self._allowed_actions(fact),
                    note=state.get("confirmation_note") or fact.note,
                )
            )
        resolved_status = ConfirmationStatus(status)
        return ContractConfirmationResponse(
            review_id=review_id,
            confirmation_status=resolved_status,
            confirmation_revision=revision,
            facts=facts,
            questions=questions,
            unresolved_questions=questions,
            ready_for_legal_review=(resolved_status is ConfirmationStatus.COMPLETED and not questions),
        )

    def _unresolved_questions(
        self,
        extraction: ContractExtractionResult,
        snapshot: Mapping[str, Any],
    ) -> list[ConfirmationQuestion]:
        questions: list[ConfirmationQuestion] = []
        for fact in extraction.facts:
            state = snapshot["facts"].get(fact.fact_id) or self._initial_fact_state(fact)
            confirmation_state = state.get("confirmation_state", ConfirmationState.UNREVIEWED.value)
            if confirmation_state not in {
                ConfirmationState.UNREVIEWED.value,
                ConfirmationState.DEFERRED.value,
            }:
                continue
            reason = self._reason_for(fact)
            questions.append(
                ConfirmationQuestion(
                    question_id=f"question:{fact.fact_id}",
                    fact_id=fact.fact_id,
                    reason=reason,
                    question_text=self._question_text(fact, reason),
                    input_type="text",
                    required=True,
                )
            )
        return questions

    @staticmethod
    def _reason_for(fact: ContractFact) -> ConfirmationQuestionReason:
        if fact.status is FactStatus.MISSING:
            return ConfirmationQuestionReason.MISSING
        if fact.status is FactStatus.CONTRADICTED:
            return ConfirmationQuestionReason.CONTRADICTED
        if fact.status is FactStatus.AMBIGUOUS:
            return ConfirmationQuestionReason.AMBIGUOUS
        if not fact.evidence:
            return ConfirmationQuestionReason.NO_EVIDENCE
        return ConfirmationQuestionReason.LOW_CONFIDENCE

    @staticmethod
    def _question_text(fact: ContractFact, reason: ConfirmationQuestionReason) -> str:
        if reason is ConfirmationQuestionReason.MISSING:
            return f"请补充或确认合同中的“{fact.name}”信息。"
        if reason is ConfirmationQuestionReason.CONTRADICTED:
            return f"合同中“{fact.name}”出现不一致，请确认以哪一处内容为准。"
        if reason is ConfirmationQuestionReason.NO_EVIDENCE:
            return f"请确认“{fact.name}”的原文位置和具体内容。"
        return f"请确认合同中的“{fact.name}”是否准确。"

    def _status_from_record(
        self,
        record: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        extraction: ContractExtractionResult,
    ) -> str:
        raw_status = record.get("confirmation_status")
        if raw_status:
            return str(raw_status)
        unresolved = self._unresolved_questions(extraction, snapshot)
        return ConfirmationStatus.PENDING.value if unresolved else ConfirmationStatus.NOT_STARTED.value
