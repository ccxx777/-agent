"""合同上传、PDF 解析、脱敏和任务状态服务。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.infrastructure.contract_document import (
    SUPPORTED_CONTRACT_SUFFIXES,
    ContractDocumentParser,
)
from app.infrastructure.contract_ocr import ContractOCRClient, ContractOCRUnavailable
from app.infrastructure.contract_pdf import PDFParseError
from app.infrastructure.contract_review_repository import ContractReviewRepository
from app.infrastructure.contract_storage import PrivateContractStorage
from app.schemas.contract_extraction import ContractExtractionResult, ExtractionStatus
from app.schemas.contract_review import ContractReviewDetail, ContractReviewSummary
from app.services.contract_extraction_service import ContractExtractionError
from app.services.privacy_redaction import desensitize_text

logger = logging.getLogger(__name__)

_DEFAULT_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ContractUploadError(ValueError):
    """上传文件不符合合同文档接入策略。"""

    status_code = 400


class ContractReviewService:
    """合同文件接入的应用服务。

    API 只负责创建任务；解析在后台任务中执行并把结果写入 PostgreSQL。进程
    重启后，``resume_pending`` 可重新接管 queued/extracting 任务。
    """

    def __init__(
        self,
        repository: ContractReviewRepository,
        storage: PrivateContractStorage,
        parser: ContractDocumentParser,
        ocr_client: ContractOCRClient,
        *,
        max_upload_bytes: int,
        max_pages: int,
        extraction_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.parser = parser
        self.ocr_client = ocr_client
        self.max_upload_bytes = max_upload_bytes
        self.max_pages = max_pages
        self.extraction_service = extraction_service

    async def create_review(
        self,
        *,
        user_id: str,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ContractReviewSummary:
        safe_filename = Path(filename or "contract.pdf").name
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in SUPPORTED_CONTRACT_SUFFIXES:
            raise ContractUploadError("当前支持 PDF、DOC 和 DOCX 合同")
        if len(content) == 0:
            raise ContractUploadError("合同文件为空")
        if len(content) > self.max_upload_bytes:
            raise ContractUploadError(f"合同文件不能超过 {self.max_upload_bytes // (1024 * 1024)} MB")
        if suffix == ".pdf" and not content.startswith(b"%PDF-"):
            raise ContractUploadError("文件不是有效的 PDF")
        resolved_content_type = content_type or _DEFAULT_CONTENT_TYPES[suffix]

        review_id = str(uuid.uuid4())
        path = self.storage.save(review_id, content, suffix=suffix)
        try:
            inspection = await asyncio.to_thread(self.parser.inspect, path)
            if inspection.page_count == 0:
                raise ContractUploadError("合同没有可读取的页面")
            if inspection.page_count > self.max_pages:
                raise ContractUploadError(f"合同页数不能超过 {self.max_pages} 页")
            await self.repository.create_task(
                {
                    "review_id": review_id,
                    "user_id": user_id,
                    "filename": safe_filename,
                    "content_type": resolved_content_type,
                    "size_bytes": len(content),
                    "sha256": sha256(content).hexdigest(),
                    "storage_path": str(path),
                    "page_count": inspection.page_count,
                }
            )
        except PDFParseError as error:
            self.storage.delete(review_id)
            raise ContractUploadError(str(error)) from error
        except Exception:
            self.storage.delete(review_id)
            raise

        return ContractReviewSummary(
            review_id=review_id,
            status="queued",
            filename=safe_filename,
            content_type=resolved_content_type,
            size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
            page_count=inspection.page_count,
        )

    async def process_review(self, review_id: str, *, storage_path: str | Path) -> None:
        """处理一份已经入库的合同文档；失败只写入任务状态，不泄露合同正文。"""

        await self.repository.mark_status(review_id, "extracting")
        try:
            inspection = await asyncio.to_thread(self.parser.inspect, storage_path)
            pages: list[dict[str, Any]] = []
            failed_pages: list[int] = []
            suspicious_pages: list[int] = []
            redaction_counts = {"id_card": 0, "phone": 0, "bank_card": 0}
            invisible_count = 0
            ocr_pages = 0
            text_pages = 0
            external_raw_image_sent = False

            supports_ocr = getattr(self.parser, "supports_ocr", lambda _path: True)(storage_path)
            for inspected_page in inspection.pages:
                page_no = inspected_page.page_no
                text = inspected_page.text
                flags = list(inspected_page.quality_flags)
                ocr_used = False

                if inspected_page.mode in {"hybrid", "scanned"}:
                    if not supports_ocr:
                        flags.append("ocr_not_supported_for_format")
                    elif self.ocr_client.enabled:
                        try:
                            image_bytes = await asyncio.to_thread(
                                self.parser.render_page, storage_path, page_no
                            )
                            text_from_ocr = await self.ocr_client.extract(image_bytes)
                            external_raw_image_sent = True
                            ocr_used = True
                            ocr_pages += 1
                            if inspected_page.mode == "hybrid" and text.strip():
                                text = f"{text.rstrip()}\n{text_from_ocr.lstrip()}"
                            else:
                                text = text_from_ocr
                            flags = [flag for flag in flags if flag != "ocr_candidate"]
                        except ContractOCRUnavailable:
                            failed_pages.append(page_no)
                            flags.append("ocr_failed")
                    else:
                        flags.append("ocr_not_configured")

                redacted = desensitize_text(text)
                text = redacted.text
                for category, count in redacted.counts.items():
                    redaction_counts[category] += count
                invisible_count += redacted.invisible_sequences_detected

                if text.strip():
                    text_pages += 1
                else:
                    flags.append("empty_text")
                if inspected_page.mode in {"hybrid", "scanned"} and not ocr_used:
                    suspicious_pages.append(page_no)
                if "ocr_failed" in flags:
                    suspicious_pages.append(page_no)
                if "format_page_boundary_unavailable" in flags:
                    suspicious_pages.append(page_no)

                pages.append(
                    {
                        "page_no": page_no,
                        "mode": inspected_page.mode,
                        "text": text,
                        "ocr_used": ocr_used,
                        "quality_flags": sorted(set(flags)),
                    }
                )

            quality = {
                "page_count": inspection.page_count,
                "text_pages": text_pages,
                "native_pages": sum(page.mode == "native" for page in inspection.pages),
                "hybrid_pages": sum(page.mode == "hybrid" for page in inspection.pages),
                "scanned_pages": sum(page.mode == "scanned" for page in inspection.pages),
                "ocr_pages": ocr_pages,
                "failed_pages": sorted(set(failed_pages)),
                "suspicious_pages": sorted(set(suspicious_pages)),
                "text_coverage": round(text_pages / inspection.page_count, 4),
                "needs_confirmation": bool(failed_pages or suspicious_pages),
            }
            privacy = {
                "redaction_version": "v1",
                "redaction_counts": redaction_counts,
                "zero_width_sequences_detected": invisible_count,
                "external_raw_image_sent": external_raw_image_sent,
            }
            status = "needs_confirmation" if quality["needs_confirmation"] else "ready"
            await self.repository.save_result(
                review_id,
                status=status,
                quality=quality,
                privacy=privacy,
                pages=pages,
            )
            if self.extraction_service is not None:
                # 只把已经脱敏的页文本交给事实提取服务；原始文件仍留在私有存储中。
                try:
                    await self.extraction_service.process(review_id, pages)
                except ContractExtractionError:
                    # 文档解析已经成功；事实提取失败只影响 extraction_status，允许用户重试。
                    logger.warning("Contract fact extraction deferred: review_id=%s", review_id)
                except Exception:
                    logger.exception("Contract fact extraction infrastructure unavailable: review_id=%s", review_id)
            logger.info("Contract review parsed: review_id=%s status=%s", review_id, status)
        except (PDFParseError, OSError, ValueError) as error:
            await self.repository.mark_status(review_id, "failed", "合同解析失败，请重新上传文件")
            logger.warning("Contract review failed: review_id=%s error=%s", review_id, type(error).__name__)
        except Exception:
            await self.repository.mark_status(review_id, "failed", "合同解析服务暂时不可用")
            logger.exception("Unexpected contract review failure: review_id=%s", review_id)

    async def get_review(self, review_id: str, user_id: str) -> ContractReviewDetail | None:
        record = await self.repository.get_task(review_id, user_id)
        if record is None:
            return None
        return ContractReviewDetail(
            review_id=str(record["review_id"]),
            status=record["status"],
            filename=record["filename"],
            content_type=record["content_type"],
            size_bytes=record["size_bytes"],
            sha256=record["sha256"],
            page_count=record["page_count"],
            quality=record.get("quality"),
            privacy=record.get("privacy"),
            extraction_status=record.get("extraction_status") or ExtractionStatus.NOT_STARTED.value,
            error_message=record.get("error_message"),
            created_at=record.get("created_at"),
            updated_at=record.get("updated_at"),
            pages=record.get("pages", []),
            extraction=(
                ContractExtractionResult.model_validate(record["extraction_result"])
                if record.get("extraction_result")
                else None
            ),
        )

    async def resume_pending(self) -> None:
        """进程启动时接管上次未完成的任务。"""

        try:
            pending = await self.repository.list_pending()
        except Exception:  # noqa: BLE001 - schema absence must not block API startup
            # 旧部署在手动执行迁移前可能还没有新表，不能阻塞 Backend 启动。
            logger.warning("Contract review recovery skipped; schema may not be migrated")
            return

        if pending:
            logger.info("Contract review tasks pending recovery: count=%s", len(pending))
        for record in pending:
            await self.process_review(
                str(record["review_id"]),
                storage_path=record["storage_path"],
            )
        if self.extraction_service is not None:
            await self.extraction_service.resume_pending()
