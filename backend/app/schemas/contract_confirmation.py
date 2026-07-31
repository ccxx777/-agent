"""合同事实确认层的数据契约。

确认层不重新调用大模型，也不修改 ``ContractFact.value``。它只记录用户对提取结果
的选择，并计算一个带 provenance 的有效事实快照，供后续规则引擎消费。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contract_extraction import ContractEvidence, FactStatus


class ConfirmationAction(str, Enum):
    """首版允许用户执行的五类动作。"""

    CONFIRM = "confirm"
    CORRECT = "correct"
    SUPPLEMENT = "supplement"
    NOT_APPLICABLE = "not_applicable"
    DEFER = "defer"


class ConfirmationState(str, Enum):
    """单个事实在确认工作流中的状态。"""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    SUPPLEMENTED = "supplemented"
    NOT_APPLICABLE = "not_applicable"
    DEFERRED = "deferred"


class EffectiveSource(str, Enum):
    """有效值来自合同、用户补充，或没有适用值。"""

    CONTRACT = "contract"
    USER = "user"
    NONE = "none"


class ConfirmationStatus(str, Enum):
    """整份合同的确认状态；与文件解析/事实提取状态分开。"""

    NOT_STARTED = "not_started"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ConfirmationQuestionReason(str, Enum):
    MISSING = "missing"
    LOW_CONFIDENCE = "low_confidence"
    NO_EVIDENCE = "no_evidence"
    CONTRADICTED = "contradicted"
    AMBIGUOUS = "ambiguous"


class ConfirmationQuestion(BaseModel):
    """与事实绑定的待确认问题，避免前端只能按字符串猜测目标字段。"""

    question_id: str
    fact_id: str
    reason: ConfirmationQuestionReason
    question_text: str
    input_type: str = "text"
    required: bool = True
    options: list[str] = Field(default_factory=list)


class FactConfirmationItem(BaseModel):
    """一次提交中针对一个事实的用户动作。"""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(..., min_length=1)
    action: ConfirmationAction
    value: Any = None
    note: str | None = Field(default=None, max_length=2000)


class FactConfirmationRequest(BaseModel):
    """事实确认草稿/提交请求。

    ``base_revision`` 是乐观锁版本；``request_id`` 用于客户端重试时保持幂等。
    """

    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(default=0, ge=0)
    items: list[FactConfirmationItem] = Field(default_factory=list)
    submit: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class FactConfirmationView(BaseModel):
    """前端表单使用的单条事实视图。

    ``original_value`` 永远来自模型提取结果；``user_value`` 永远是用户输入；
    ``effective_value`` 是当前允许规则引擎读取的值，三者不互相覆盖。
    """

    fact_id: str
    field_key: str = "other"
    category: str
    name: str
    original_value: Any = None
    normalized_original_value: Any = None
    user_value: Any = None
    effective_value: Any = None
    effective_source: EffectiveSource = EffectiveSource.NONE
    confirmation_state: ConfirmationState = ConfirmationState.UNREVIEWED
    extraction_status: FactStatus
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[ContractEvidence] = Field(default_factory=list)
    source_clause_ids: list[str] = Field(default_factory=list)
    question_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[ConfirmationAction] = Field(
        default_factory=lambda: list(ConfirmationAction)
    )
    note: str | None = None


class ContractConfirmationResponse(BaseModel):
    """事实确认查询和提交共用的响应格式。"""

    review_id: str
    confirmation_status: ConfirmationStatus
    confirmation_revision: int = Field(..., ge=0)
    facts: list[FactConfirmationView] = Field(default_factory=list)
    questions: list[ConfirmationQuestion] = Field(default_factory=list)
    unresolved_questions: list[ConfirmationQuestion] = Field(default_factory=list)
    ready_for_legal_review: bool = False
