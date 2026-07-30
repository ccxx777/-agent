"""合同审查 Workflow 的 LangGraph 编排。

这张图与通用聊天 Agent 分开：合同审查首版是有明确门禁的 Workflow，
而不是允许模型自由调用工具的对话循环。每个节点只做一件事，便于后续
加入规则版本、人工复核和报告持久化，而不改变上传/提取/确认模块。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.schemas.contract_confirmation import ContractConfirmationResponse
from app.schemas.contract_extraction import ContractExtractionResult
from app.schemas.contract_review_workflow import (
    ContractReviewReport,
    ContractReviewWorkflowResponse,
    FindingType,
    LegalSource,
    RuleFinding,
    WorkflowStatus,
)
from app.services.contract_rule_engine import ContractRuleEngine

logger = logging.getLogger(__name__)


class ContractReviewWorkflowState(TypedDict, total=False):
    """节点间只传递已经脱敏或结构化的数据。"""

    review_id: str
    user_id: str
    confirmation: ContractConfirmationResponse
    extraction: ContractExtractionResult
    facts: list[dict[str, Any]]
    scope: str
    workflow_status: WorkflowStatus
    findings: list[RuleFinding]
    legal_sources: list[LegalSource]
    case_sources: list[LegalSource]
    pending_questions: list[str]
    warnings: list[str]
    report: ContractReviewReport


class ContractReviewWorkflowError(RuntimeError):
    """Workflow 无法安全完成时抛出的业务错误。"""


class ContractReviewWorkflowNodes:
    """合同审查图的节点集合；依赖全部由 Composition Root 注入。"""

    def __init__(
        self,
        *,
        repository: Any,
        confirmation_service: Any,
        legal_retrieval_service: Any | None,
        case_retrieval_service: Any | None,
        rule_engine: ContractRuleEngine | None = None,
    ) -> None:
        self.repository = repository
        self.confirmation_service = confirmation_service
        self.legal_retrieval_service = legal_retrieval_service
        self.case_retrieval_service = case_retrieval_service
        self.rule_engine = rule_engine or ContractRuleEngine()

    async def load_facts(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        record = await self.repository.get_task(state["review_id"], state["user_id"])
        if record is None:
            raise ContractReviewWorkflowError("合同审查任务不存在或无权访问")
        confirmation = await self.confirmation_service.get_confirmation(
            state["review_id"], state["user_id"]
        )
        if confirmation is None:
            raise ContractReviewWorkflowError("合同事实确认结果不存在")
        raw_extraction = record.get("extraction_result")
        if not raw_extraction:
            raise ContractReviewWorkflowError("合同事实尚未提取完成")
        try:
            extraction = ContractExtractionResult.model_validate(raw_extraction)
        except ValueError as error:
            raise ContractReviewWorkflowError("合同事实结果格式不可用") from error

        facts = [self._fact_snapshot(fact) for fact in confirmation.facts]
        warnings = list(extraction.warnings)
        return {
            "confirmation": confirmation,
            "extraction": extraction,
            "facts": facts,
            "warnings": warnings,
        }

    async def confirmation_gate(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        confirmation = state["confirmation"]
        if not confirmation.ready_for_legal_review:
            pending = [question.question_text for question in confirmation.unresolved_questions]
            return {
                "workflow_status": WorkflowStatus.AWAITING_CONFIRMATION,
                "pending_questions": list(dict.fromkeys(pending)),
            }
        return {}

    def route_after_confirmation(self, state: ContractReviewWorkflowState) -> str:
        status = state.get("workflow_status")
        return (
            "awaiting_confirmation"
            if getattr(status, "value", status) == WorkflowStatus.AWAITING_CONFIRMATION.value
            else "continue"
        )

    async def scope_check(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        text = " ".join(
            str(item.get(key) or "")
            for item in state.get("facts", [])
            for key in ("category", "name", "value")
        ).lower()
        clause_types = {clause.clause_type.value for clause in state["extraction"].clauses}
        labor_signals = (
            "劳动" in text
            or "用人单位" in text
            or "试用期" in text
            or "社会保险" in text
            or "social" in text
            or "probation" in text
            or "employment" in text
            or bool(
                clause_types
                & {
                    "parties",
                    "probation",
                    "compensation",
                    "social_insurance",
                    "work_hours",
                }
            )
        )
        if not labor_signals:
            return {
                "scope": "unsupported_or_unconfirmed",
                "workflow_status": WorkflowStatus.OUT_OF_SCOPE,
                "warnings": [
                    "当前版本仅审查中国大陆全国通用劳动合同规则，未能从已确认事实中识别劳动合同范围。"
                ],
            }
        return {"scope": "labor_contract_national"}

    def route_after_scope(self, state: ContractReviewWorkflowState) -> str:
        status = state.get("workflow_status")
        return "out_of_scope" if getattr(status, "value", status) == WorkflowStatus.OUT_OF_SCOPE.value else "continue"

    async def evaluate_rules(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        findings = self.rule_engine.evaluate(state.get("facts", []))
        return {"findings": findings}

    async def retrieve_law(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        queries = self.rule_engine.queries_for_findings(state.get("findings", []))
        if not queries:
            # 即使当前没有显式规则命中，也检索一条全国通用基线，避免“无命中”
            # 被误读为“无风险”或绕过 A 级资料可用性检查。
            queries = [("LC-BASE", "中国大陆劳动合同订立必备事项和劳动者基本权益")]
        if self.legal_retrieval_service is None:
            return {"warnings": ["A 级法律资料库尚未配置，本次仅返回事实层提示。"]}

        sources, warnings = await self._retrieve_sources(
            self.legal_retrieval_service,
            queries,
            source_level="A",
        )
        return {"legal_sources": sources, "warnings": warnings}

    async def retrieve_cases(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        queries = [
            (finding.rule_id, f"劳动争议案例 {finding.title} {finding.summary}")
            for finding in state.get("findings", [])
            if finding.finding_type is FindingType.POSSIBLE_CONFLICT
        ][:3]
        if not queries or self.case_retrieval_service is None:
            return {}
        sources, warnings = await self._retrieve_sources(
            self.case_retrieval_service,
            queries,
            source_level="B",
        )
        return {"case_sources": sources, "warnings": warnings}

    async def compose_report(self, state: ContractReviewWorkflowState) -> dict[str, Any]:
        pending = list(state.get("pending_questions", []))
        for finding in state.get("findings", []):
            if finding.question:
                pending.append(finding.question)
        pending = list(dict.fromkeys(pending))

        warnings = list(dict.fromkeys(state.get("warnings", [])))
        if not state.get("findings", []):
            warnings.append(
                "当前规则卡片未命中显式提示，不等于合同无风险；仍需结合完整法律资料和专业复核。"
            )
        workflow_status = state.get("workflow_status")
        if workflow_status is None:
            workflow_status = (
                WorkflowStatus.PARTIAL
                if state.get("warnings")
                else WorkflowStatus.COMPLETED
            )
        report = ContractReviewReport(
            review_id=state["review_id"],
            workflow_status=workflow_status,
            scope=state.get("scope", "labor_contract_national"),
            generated_at=datetime.now(timezone.utc),
            findings=state.get("findings", []),
            pending_questions=pending,
            legal_sources=state.get("legal_sources", []),
            case_sources=state.get("case_sources", []),
            warnings=warnings,
        )
        return {"report": report}

    @staticmethod
    def _fact_snapshot(fact: Any) -> dict[str, Any]:
        """从确认视图复制有效值和证据，不修改提取原值。"""

        state = getattr(fact.confirmation_state, "value", fact.confirmation_state)
        effective = fact.effective_value
        resolved = state in {"confirmed", "corrected", "supplemented", "not_applicable"}
        return {
            "fact_id": fact.fact_id,
            "category": fact.category,
            "name": fact.name,
            "value": effective if resolved else None,
            "original_value": fact.original_value,
            "effective_source": getattr(fact.effective_source, "value", fact.effective_source),
            "confirmation_state": state,
            "extraction_status": getattr(fact.extraction_status, "value", fact.extraction_status),
            "evidence": list(fact.evidence),
        }

    @staticmethod
    async def _retrieve_sources(
        service: Any,
        queries: list[tuple[str, str]],
        *,
        source_level: str,
    ) -> tuple[list[LegalSource], list[str]]:
        sources: list[LegalSource] = []
        warnings: list[str] = []
        seen: set[tuple[str, str]] = set()
        for rule_id, query in queries:
            try:
                payload = await service.retrieve(query)
            except Exception:  # noqa: BLE001 - 资料库不可用时降级为 partial
                logger.warning("Legal retrieval failed: level=%s query=%s", source_level, query)
                warnings.append(f"{source_level} 级资料检索暂时不可用，未将检索失败当作无风险。")
                continue
            if not payload.documents:
                warnings.append(f"{source_level} 级资料检索未返回可引用文档，未将空结果当作无风险。")
                continue
            for document in payload.documents:
                key = (document.doc_id, document.chunk_id or document.point_id)
                if key in seen:
                    continue
                seen.add(key)
                sources.append(
                    LegalSource(
                        source_level=source_level,
                        rule_id=rule_id,
                        query=query,
                        doc_id=document.doc_id,
                        chunk_id=document.chunk_id,
                        title=document.title,
                        source=document.source,
                        rank=document.rank,
                        quote=document.text or document.context_text,
                    )
                )
        return sources, list(dict.fromkeys(warnings))


def build_contract_review_workflow(
    *,
    repository: Any,
    confirmation_service: Any,
    legal_retrieval_service: Any | None,
    case_retrieval_service: Any | None,
    rule_engine: ContractRuleEngine | None = None,
) -> Any:
    """构建无 Checkpointer 的 v0.1 审查图；报告持久化留给下一阶段。"""

    nodes = ContractReviewWorkflowNodes(
        repository=repository,
        confirmation_service=confirmation_service,
        legal_retrieval_service=legal_retrieval_service,
        case_retrieval_service=case_retrieval_service,
        rule_engine=rule_engine,
    )
    workflow = StateGraph(ContractReviewWorkflowState)
    workflow.add_node("load_facts", nodes.load_facts)
    workflow.add_node("confirmation_gate", nodes.confirmation_gate)
    workflow.add_node("scope_check", nodes.scope_check)
    workflow.add_node("evaluate_rules", nodes.evaluate_rules)
    workflow.add_node("retrieve_law", nodes.retrieve_law)
    workflow.add_node("retrieve_cases", nodes.retrieve_cases)
    workflow.add_node("compose_report", nodes.compose_report)

    workflow.add_edge(START, "load_facts")
    workflow.add_edge("load_facts", "confirmation_gate")
    workflow.add_conditional_edges(
        "confirmation_gate",
        nodes.route_after_confirmation,
        {"awaiting_confirmation": "compose_report", "continue": "scope_check"},
    )
    workflow.add_conditional_edges(
        "scope_check",
        nodes.route_after_scope,
        {"out_of_scope": "compose_report", "continue": "evaluate_rules"},
    )
    workflow.add_edge("evaluate_rules", "retrieve_law")
    workflow.add_edge("retrieve_law", "retrieve_cases")
    workflow.add_edge("retrieve_cases", "compose_report")
    workflow.add_edge("compose_report", END)
    return workflow.compile()


class ContractReviewWorkflowService:
    """API 与 LangGraph 之间的薄服务边界。"""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def run(self, review_id: str, user_id: str) -> ContractReviewWorkflowResponse:
        try:
            state = await self.graph.ainvoke(
                {"review_id": review_id, "user_id": user_id}
            )
        except ContractReviewWorkflowError:
            raise
        except Exception as error:
            logger.exception("Contract review workflow failed: review_id=%s", review_id)
            raise ContractReviewWorkflowError("合同审查 Workflow 暂时不可用") from error
        report = state.get("report")
        if not isinstance(report, ContractReviewReport):
            report = ContractReviewReport.model_validate(report)
        return ContractReviewWorkflowResponse(
            review_id=review_id,
            workflow_status=report.workflow_status,
            report=report,
        )
