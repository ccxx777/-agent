"""基于 PyMuPDF 的合同 PDF 分页检查和渲染。

这里不调用外部模型，只回答两个问题：页面有没有可用文字，以及哪些页面
需要 OCR。这样原生 PDF 可以快速进入脱敏流程，扫描页则由可插拔的 OCR
Provider 处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

TEXT_PAGE_MIN_CHARS = 50
IMAGE_PAGE_MAX_CHARS = 20
HYBRID_IMAGE_AREA_THRESHOLD = 0.10
RENDER_DPI = 200


class PDFParseError(ValueError):
    """PDF 无法打开、加密或无法读取。"""


@dataclass(frozen=True)
class PDFPageInspection:
    page_no: int
    mode: str
    text: str
    image_area_ratio: float
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class PDFInspection:
    page_count: int
    pages: tuple[PDFPageInspection, ...]


def _image_area_ratio(page: fitz.Page) -> float:
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    image_area = 0.0
    images = page.get_images(full=True)
    if not images:
        return 0.0
    for image in images:
        for rectangle in page.get_image_rects(image, transform=True):
            image_area += max(float(rectangle.get_area()), 0.0)
    return min(image_area / page_area, 1.0)


def _classify_page(page: fitz.Page, text: str, image_area_ratio: float) -> str:
    char_count = len("".join(text.split()))
    if char_count >= TEXT_PAGE_MIN_CHARS and image_area_ratio < HYBRID_IMAGE_AREA_THRESHOLD:
        return "native"
    if char_count >= IMAGE_PAGE_MAX_CHARS:
        return "hybrid"
    return "scanned"


class ContractPDFParser:
    """执行 PDF 元数据检查、分页分类和指定页面渲染。"""

    def inspect(self, path: str | Path) -> PDFInspection:
        try:
            document = fitz.open(str(path))
        except (RuntimeError, ValueError, fitz.FileDataError) as error:
            raise PDFParseError("PDF 文件无法读取") from error

        try:
            if document.needs_pass:
                raise PDFParseError("PDF 受密码保护，暂时无法解析")
            pages: list[PDFPageInspection] = []
            for index, page in enumerate(document, start=1):
                text = page.get_text("text") or ""
                image_ratio = _image_area_ratio(page)
                mode = _classify_page(page, text, image_ratio)
                flags: list[str] = []
                if mode != "native":
                    flags.append("ocr_candidate")
                if not text.strip():
                    flags.append("no_native_text")
                if image_ratio >= HYBRID_IMAGE_AREA_THRESHOLD:
                    flags.append("large_image_region")
                pages.append(
                    PDFPageInspection(
                        page_no=index,
                        mode=mode,
                        text=text,
                        image_area_ratio=round(image_ratio, 4),
                        quality_flags=tuple(flags),
                    )
                )
            return PDFInspection(page_count=len(pages), pages=tuple(pages))
        finally:
            document.close()

    def render_page(self, path: str | Path, page_no: int, *, dpi: int = RENDER_DPI) -> bytes:
        if page_no < 1:
            raise PDFParseError("page_no 必须从 1 开始")
        try:
            document = fitz.open(str(path))
        except (RuntimeError, ValueError, fitz.FileDataError) as error:
            raise PDFParseError("PDF 文件无法读取") from error

        try:
            if document.needs_pass:
                raise PDFParseError("PDF 受密码保护，暂时无法解析")
            if page_no > len(document):
                raise PDFParseError("页码超出 PDF 范围")
            matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pixmap = document[page_no - 1].get_pixmap(matrix=matrix, alpha=False)
            return pixmap.tobytes("jpg", jpg_quality=90)
        except (RuntimeError, ValueError) as error:
            if isinstance(error, PDFParseError):
                raise
            raise PDFParseError("PDF 页面渲染失败") from error
        finally:
            document.close()
