"""合同条款与事实提取的数据契约。

本模块只描述“合同里写了什么”以及证据位于哪里，不表达“是否合法”或“是否应该签署”。
后续的规则引擎可以在这些事实之上独立计算风险等级，避免把事实抽取和法律判断混在一个模型输出中。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractionStatus(str, Enum):
    """事实提取任务的状态，与文件解析状态分开。"""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    READY = "ready"
    NEEDS_CONFIRMATION = "needs_confirmation"
    FAILED = "failed"


class FactStatus(str, Enum):
    """事实的确定程度，不等同于法律风险等级。"""

    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    CONTRADICTED = "contradicted"
    NEEDS_CONFIRMATION = "needs_confirmation"


class RequiredFactField:
    """劳动合同首版必须覆盖的事实字段定义。

    这些字段只表示“需要检查合同是否写明”，不表示合同一定应当包含
    对应内容。若正文没有出现，模型和本地兜底都必须返回 ``missing``
    事实，不能静默省略。
    """

    def __init__(self, field_key: str, category: str, name: str) -> None:
        self.field_key = field_key
        self.category = category
        self.name = name


LABOR_REQUIRED_FACT_FIELDS: tuple[RequiredFactField, ...] = (
    RequiredFactField("employer", "当事人", "用人单位"),
    RequiredFactField("employee", "当事人", "劳动者"),
    RequiredFactField("contract_term", "期限", "劳动合同期限"),
    RequiredFactField("probation_period", "期限", "试用期"),
    RequiredFactField("work_content", "工作内容/地点", "工作内容"),
    RequiredFactField("work_location", "工作内容/地点", "工作地点"),
    RequiredFactField("salary", "劳动报酬", "工资报酬"),
    RequiredFactField("working_hours", "工时休假", "工作时间"),
    RequiredFactField("overtime", "工时休假", "加班及补偿"),
    RequiredFactField("leave", "工时休假", "休假"),
    RequiredFactField("social_insurance", "社会保险", "社会保险"),
    RequiredFactField("housing_fund", "社会保险", "住房公积金"),
    RequiredFactField("termination", "解除终止", "解除或终止"),
    RequiredFactField("training_liability", "违约责任", "培训服务期及费用"),
    RequiredFactField("liquidated_damages", "违约责任", "违约金"),
    RequiredFactField("non_compete", "保密/知识产权", "竞业限制"),
    RequiredFactField("confidentiality", "保密/知识产权", "保密义务"),
    RequiredFactField("dispute_resolution", "劳动争议解决", "争议解决"),
)

LABOR_REQUIRED_FACT_KEYS = frozenset(item.field_key for item in LABOR_REQUIRED_FACT_FIELDS)


class ClauseType(str, Enum):
    """劳动合同首版关注的条款类别。"""

    PARTIES = "parties"
    TERM = "term"
    PROBATION = "probation"
    WORK_CONTENT = "work_content"
    WORK_LOCATION = "work_location"
    COMPENSATION = "compensation"
    WORK_HOURS = "work_hours"
    LEAVE = "leave"
    SOCIAL_INSURANCE = "social_insurance"
    TERMINATION = "termination"
    LIABILITY = "liability"
    NON_COMPETE = "non_compete"
    CONFIDENTIALITY = "confidentiality"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    DISPUTE_RESOLUTION = "dispute_resolution"
    OTHER = "other"


class EvidenceMatchType(str, Enum):
    """证据定位使用的匹配方式，便于前端解释定位可信度。"""

    EXACT = "exact"
    NORMALIZED = "normalized"


class ContractEvidence(BaseModel):
    """脱敏合同中的一个可回溯证据片段。"""

    page_no: int = Field(..., ge=1)
    quote: str = Field(..., min_length=1)
    char_start: int = Field(..., ge=0)
    char_end: int = Field(..., gt=0)
    match_type: EvidenceMatchType = EvidenceMatchType.EXACT
    clause_id: str | None = None


class ContractClause(BaseModel):
    """由确定性切分器生成的条款块，文本已经是脱敏后的文本。"""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    clause_type: ClauseType = ClauseType.OTHER
    title: str
    text: str
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)
    source_page_nos: list[int] = Field(default_factory=list)


class ContractFactDraft(BaseModel):
    """LLM 返回的中间事实格式。

    ``evidence_quotes`` 只是模型提供的候选引用，必须经过本地 EvidenceLocator
    在脱敏原文中重新定位后，才能进入最终 ``ContractFact``。
    """

    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    value: Any = Field(...)
    status: FactStatus = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    clause_ids: list[str] = Field(...)
    evidence_quotes: list[str] = Field(...)
    needs_confirmation: bool = Field(...)
    note: str | None = Field(...)


class ContractFactExtractionPayload(BaseModel):
    """LLM 返回的最外层 JSON 契约。

    ``facts`` 暂时保留为 ``Any`` 列表，是为了允许服务逐条跳过坏记录并
    继续生成其余事实；每一条记录随后仍由 ``ContractFactDraft`` 严格校验。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    facts: list[Any]


class ContractFact(BaseModel):
    """经过本地校验和证据定位后的结构化事实。

    ``value`` 是不可变的模型原始值。确认层可以在旁边写入用户值和有效值，
    但不会回写或删除该原始值及其证据。
    """

    fact_id: str
    field_key: str = "other"
    category: str
    name: str
    value: Any = None
    normalized_value: Any = None
    status: FactStatus
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[ContractEvidence] = Field(default_factory=list)
    source_clause_ids: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    note: str | None = None
    user_value: Any = None
    effective_value: Any = None
    effective_source: str = "none"
    confirmation_state: str = "unreviewed"
    confirmation_note: str | None = None


class ContractExtractionResult(BaseModel):
    """合同事实提取结果；不包含法律风险结论。"""

    extraction_status: ExtractionStatus
    extraction_mode: str = "batch"
    model_calls: int = 0
    invalid_fact_count: int = 0
    clauses: list[ContractClause] = Field(default_factory=list)
    facts: list[ContractFact] = Field(default_factory=list)
    confirmation_questions: list[str] = Field(default_factory=list)
    confirmation_question_items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    model: str | None = None
    extracted_at: datetime | None = None
