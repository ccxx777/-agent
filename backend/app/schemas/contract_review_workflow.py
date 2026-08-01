"""合同审查 Workflow 的可解释输出契约。

Workflow 的输出刻意拆成 ``findings``、``legal_sources`` 和 ``pending_questions``：
规则节点负责判断“合同事实是否触发了检查项”，检索节点负责提供官方依据，
而不是让一个黑盒模型直接输出“违法/应该签署”。这也是首版面向个人用户时的
安全边界：报告提供参考性意见，不替用户作签署决定，也不构成律师意见。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contract_extraction import ContractEvidence


class WorkflowStatus(str, Enum):
    """一次审查运行的最终状态。"""

    COMPLETED = "completed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PARTIAL = "partial"
    OUT_OF_SCOPE = "out_of_scope"
    FAILED = "failed"


class RiskLevel(str, Enum):
    """风险等级只描述当前证据下的提示强度，不等同于司法结论。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCONFIRMED = "unconfirmed"
    INFO = "info"


class FindingType(str, Enum):
    """报告中一条发现的来源类型。"""

    MISSING_INFORMATION = "missing_information"
    POSSIBLE_CONFLICT = "possible_conflict"
    SCOPE_WARNING = "scope_warning"
    OBSERVATION = "observation"


class LegalSource(BaseModel):
    """一条可回溯的 A 级法律或 B 级案例检索结果。"""

    model_config = ConfigDict(extra="forbid")

    source_level: str = Field(..., pattern=r"^[AB]$")
    rule_id: str | None = None
    query: str
    doc_id: str = ""
    chunk_id: str = ""
    title: str = ""
    source: str = ""
    rank: int = Field(..., ge=1)
    quote: str = ""
    citation_label: str = ""
    official_url: str = ""
    effective_date: str = ""
    citation_eligible: bool | None = None
    legal_activation_status: str = ""


class RuleFinding(BaseModel):
    """规则卡片在本次合同上的一次可解释命中。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    title: str
    finding_type: FindingType
    risk_level: RiskLevel
    summary: str
    fact_ids: list[str] = Field(default_factory=list)
    legal_references: list[str] = Field(default_factory=list)
    evidence: list[ContractEvidence] = Field(default_factory=list)
    recommendation: str = ""
    question: str | None = None


class ContractReviewReport(BaseModel):
    """合同审查 Workflow 的稳定报告格式。"""

    model_config = ConfigDict(extra="forbid")

    review_id: str
    session_id: str | None = None
    report_id: str | None = None
    report_version: int = Field(default=1, ge=1)
    workflow_status: WorkflowStatus
    scope: str
    generated_at: datetime
    findings: list[RuleFinding] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)
    legal_sources: list[LegalSource] = Field(default_factory=list)
    case_sources: list[LegalSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "本报告仅基于当前提交的合同文本、用户确认事实和所示法律资料提供参考性意见，"
        "不构成律师意见、诉讼策略或签署决定，也不对最终法律结果作任何担保。"
    )


class ContractReviewWorkflowResponse(BaseModel):
    """API 返回的 Workflow 包装对象，便于未来增加 trace 和版本信息。"""

    review_id: str
    report_id: str | None = None
    workflow_status: WorkflowStatus
    report: ContractReviewReport
