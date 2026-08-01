"""合同上传与文档解析 API 的稳定数据契约。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.contract_confirmation import ConfirmationStatus
from app.schemas.contract_extraction import ContractExtractionResult, ExtractionStatus


class ReviewStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    READY = "ready"
    NEEDS_CONFIRMATION = "needs_confirmation"
    FAILED = "failed"


class PageMode(str, Enum):
    NATIVE = "native"
    HYBRID = "hybrid"
    SCANNED = "scanned"


class ContractPage(BaseModel):
    """一页脱敏后的合同内容；不返回未脱敏原文。"""

    page_no: int = Field(..., ge=1)
    mode: PageMode
    text: str = ""
    ocr_used: bool = False
    quality_flags: list[str] = Field(default_factory=list)


class ContractQuality(BaseModel):
    page_count: int = Field(..., ge=0)
    text_pages: int = Field(..., ge=0)
    native_pages: int = Field(..., ge=0)
    hybrid_pages: int = Field(..., ge=0)
    scanned_pages: int = Field(..., ge=0)
    ocr_pages: int = Field(..., ge=0)
    failed_pages: list[int] = Field(default_factory=list)
    suspicious_pages: list[int] = Field(default_factory=list)
    text_coverage: float = Field(..., ge=0.0, le=1.0)
    needs_confirmation: bool = False


class PrivacyReport(BaseModel):
    """只暴露脱敏统计，不暴露任何敏感值。"""

    redaction_version: str = "v1"
    redaction_counts: dict[str, int] = Field(default_factory=dict)
    zero_width_sequences_detected: int = Field(default=0, ge=0)
    external_raw_image_sent: bool = False


class ContractReviewSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    review_id: str
    session_id: str | None = None
    retention_policy: str = "short"
    expires_at: datetime | None = None
    status: ReviewStatus
    filename: str
    content_type: str
    size_bytes: int = Field(..., ge=0)
    sha256: str
    page_count: int | None = Field(default=None, ge=0)
    quality: ContractQuality | None = None
    privacy: PrivacyReport | None = None
    extraction_status: ExtractionStatus = ExtractionStatus.NOT_STARTED
    confirmation_status: ConfirmationStatus = ConfirmationStatus.NOT_STARTED
    confirmation_revision: int = Field(default=0, ge=0)
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ContractReviewDetail(ContractReviewSummary):
    pages: list[ContractPage] = Field(default_factory=list)
    extraction: ContractExtractionResult | None = None
