"""合同上传服务的无数据库单元测试。"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from app.infrastructure.contract_document import ContractDocumentParser
from app.infrastructure.contract_pdf import PDFInspection, PDFPageInspection
from app.infrastructure.contract_storage import PrivateContractStorage
from app.services.contract_review_service import ContractReviewService


class _FakeRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.pages: dict[str, list[dict]] = {}

    async def create_task(self, record: dict) -> None:
        self.tasks[record["review_id"]] = {**record, "status": "queued"}

    async def mark_status(self, review_id: str, status: str, error_message: str | None = None) -> None:
        self.tasks[review_id]["status"] = status
        self.tasks[review_id]["error_message"] = error_message

    async def save_result(self, review_id: str, *, status: str, quality: dict, privacy: dict, pages: list[dict]) -> None:
        self.tasks[review_id].update(status=status, quality=quality, privacy=privacy)
        self.pages[review_id] = pages


class _FakeParser:
    def __init__(self, inspection: PDFInspection) -> None:
        self.inspection = inspection

    def inspect(self, path: str | Path) -> PDFInspection:
        return self.inspection


class _FakeOCR:
    enabled = False


class _PurgeRepository:
    async def purge_expired(self):
        return [("00000000-0000-0000-0000-000000000001", "/private/original.pdf")]

    def __init__(self) -> None:
        self.finalize_calls = 0

    async def finalize_expired(self, review_id: str) -> bool:
        self.finalize_calls += 1
        return True


class _FailingStorage:
    def delete(self, review_id: str) -> None:
        raise OSError("disk unavailable")


class _WorkingStorage:
    def delete(self, review_id: str) -> None:
        return None


class ContractReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_creates_session_and_short_retention_metadata(self):
        inspection = PDFInspection(
            page_count=1,
            pages=(
                PDFPageInspection(
                    page_no=1,
                    mode="native",
                    text="劳动合同",
                    image_area_ratio=0.0,
                    quality_flags=(),
                ),
            ),
        )
        repository = _FakeRepository()
        with tempfile.TemporaryDirectory() as directory:
            service = ContractReviewService(
                repository,
                PrivateContractStorage(directory),
                _FakeParser(inspection),
                _FakeOCR(),
                max_upload_bytes=1024 * 1024,
                max_pages=10,
                short_retention_days=1,
            )
            summary = await service.create_review(
                user_id="00000000-0000-0000-0000-000000000001",
                filename="contract.pdf",
                content_type="application/pdf",
                content=b"%PDF-fake",
            )

        self.assertIsNotNone(summary.session_id)
        self.assertEqual(summary.retention_policy, "short")
        self.assertIsNotNone(summary.expires_at)
        self.assertEqual(repository.tasks[summary.review_id]["retention_policy"], "short")

    async def test_docx_upload_is_extracted_and_requires_format_confirmation(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>甲方：北京某公司。乙方：张三。</w:t></w:r></w:p></w:body>
        </w:document>"""
        repository = _FakeRepository()
        with tempfile.TemporaryDirectory() as directory:
            service = ContractReviewService(
                repository,
                PrivateContractStorage(directory),
                ContractDocumentParser(),
                _FakeOCR(),
                max_upload_bytes=1024 * 1024,
                max_pages=10,
            )
            with tempfile.TemporaryDirectory() as source_directory:
                source = Path(source_directory) / "劳动合同.docx"
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("word/document.xml", xml)
                summary = await service.create_review(
                    user_id="00000000-0000-0000-0000-000000000001",
                    filename=source.name,
                    content_type=None,
                    content=source.read_bytes(),
                )

            self.assertEqual(service.storage.path_for(summary.review_id).name, "original.docx")
            self.assertEqual(
                summary.content_type,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            await service.process_review(
                summary.review_id,
                storage_path=service.storage.path_for(summary.review_id),
            )

        self.assertEqual(repository.tasks[summary.review_id]["status"], "needs_confirmation")
        self.assertIn("北京某公司", repository.pages[summary.review_id][0]["text"])

    async def test_native_pdf_is_ready_and_page_text_is_redacted(self):
        inspection = PDFInspection(
            page_count=1,
            pages=(
                PDFPageInspection(
                    page_no=1,
                    mode="native",
                    text="联系人：13912345678。",
                    image_area_ratio=0.0,
                    quality_flags=(),
                ),
            ),
        )
        repository = _FakeRepository()
        with tempfile.TemporaryDirectory() as directory:
            service = ContractReviewService(
                repository, PrivateContractStorage(directory), _FakeParser(inspection), _FakeOCR(),
                max_upload_bytes=1024 * 1024, max_pages=10,
            )
            summary = await service.create_review(
                user_id="00000000-0000-0000-0000-000000000001",
                filename="劳动合同.pdf",
                content_type="application/pdf",
                content=b"%PDF-fake",
            )
            await service.process_review(summary.review_id, storage_path=service.storage.path_for(summary.review_id))

        self.assertEqual(repository.tasks[summary.review_id]["status"], "ready")
        self.assertEqual(repository.pages[summary.review_id][0]["text"], "联系人：139****5678。")

    async def test_scanned_page_without_ocr_requires_confirmation(self):
        inspection = PDFInspection(
            page_count=1,
            pages=(
                PDFPageInspection(
                    page_no=1,
                    mode="scanned",
                    text="",
                    image_area_ratio=0.9,
                    quality_flags=("ocr_candidate",),
                ),
            ),
        )
        repository = _FakeRepository()
        with tempfile.TemporaryDirectory() as directory:
            service = ContractReviewService(
                repository, PrivateContractStorage(directory), _FakeParser(inspection), _FakeOCR(),
                max_upload_bytes=1024 * 1024, max_pages=10,
            )
            summary = await service.create_review(
                user_id="00000000-0000-0000-0000-000000000001",
                filename="scan.pdf",
                content_type="application/pdf",
                content=b"%PDF-fake",
            )
            await service.process_review(summary.review_id, storage_path=service.storage.path_for(summary.review_id))

        self.assertEqual(repository.tasks[summary.review_id]["status"], "needs_confirmation")
        self.assertEqual(repository.tasks[summary.review_id]["quality"]["suspicious_pages"], [1])

    async def test_expired_cleanup_keeps_retry_record_when_file_delete_fails(self):
        repository = _PurgeRepository()
        service = ContractReviewService(
            repository,
            _FailingStorage(),
            _FakeParser(PDFInspection(page_count=0, pages=())),
            _FakeOCR(),
            max_upload_bytes=1024,
            max_pages=1,
        )

        removed = await service.purge_expired()

        self.assertEqual(removed, 0)
        self.assertEqual(repository.finalize_calls, 0)

    async def test_expired_cleanup_finalizes_after_file_delete(self):
        repository = _PurgeRepository()
        service = ContractReviewService(
            repository,
            _WorkingStorage(),
            _FakeParser(PDFInspection(page_count=0, pages=())),
            _FakeOCR(),
            max_upload_bytes=1024,
            max_pages=1,
        )

        removed = await service.purge_expired()

        self.assertEqual(removed, 1)
        self.assertEqual(repository.finalize_calls, 1)


if __name__ == "__main__":
    unittest.main()
