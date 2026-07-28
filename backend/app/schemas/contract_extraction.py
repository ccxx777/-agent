"""合同条款与事实提取的数据契约。

本模块只描述“合同里写了什么”以及证据位于哪里，不表达“是否合法”或“是否应该签署”。
后续的规则引擎可以在这些事实之上独立计算风险等级，避免把事实抽取和法律判断混在一个模型输出中。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

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

    category: str = "other"
    name: str
    value: Any = None
    status: str = "confirmed"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clause_ids: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    note: str | None = None


class ContractFact(BaseModel):
    """经过本地校验和证据定位后的结构化事实。"""

    fact_id: str
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


class ContractExtractionResult(BaseModel):
    """合同事实提取结果；不包含法律风险结论。"""

    extraction_status: ExtractionStatus
    clauses: list[ContractClause] = Field(default_factory=list)
    facts: list[ContractFact] = Field(default_factory=list)
    confirmation_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model: str | None = None
    extracted_at: datetime | None = None

