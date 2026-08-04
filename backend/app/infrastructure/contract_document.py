"""合同文档统一解析入口。

PDF 继续使用 PyMuPDF，DOCX 使用 OOXML 的纯文本结构，传统 DOC 使用
``antiword``。三种格式最后都返回同一份页级检查结果，让后续脱敏、OCR
质量门禁和审查 Workflow 不需要知道上传文件的原始扩展名。

DOC/DOCX 的格式转换不会伪装成 PDF：当前版本提取文档文字并以页级近似结果
继续处理。因为 Word 文件的真实分页、页眉页脚和浮动文本框不一定能从文字
结构中可靠恢复，所以这类结果会带上 ``format_page_boundary_unavailable``
质量标记，供用户确认后再进入风险审查。
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from defusedxml import ElementTree

from app.infrastructure.contract_pdf import (
    ContractPDFParser,
    PDFInspection,
    PDFPageInspection,
    PDFParseError,
)

SUPPORTED_CONTRACT_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
DOCX_SUFFIX = ".docx"
DOC_SUFFIX = ".doc"
PDF_SUFFIX = ".pdf"

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_STRICT_WORD_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
_SUPPORTED_WORD_NAMESPACES = frozenset({_WORD_NS, _STRICT_WORD_NS})
MAX_DOCX_XML_BYTES = 10 * 1024 * 1024


def _w(name: str, namespace: str = _WORD_NS) -> str:
    return f"{{{namespace}}}{name}"


def _namespace_from_tag(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1 : tag.index("}")]
    return None


class DocumentParseError(PDFParseError):
    """Word 文档无法读取或服务器缺少对应解析组件。"""


def _paragraph_text(element: ElementTree.Element, namespace: str = _WORD_NS) -> str:
    """提取一个段落中的文字和显式换行。"""

    parts: list[str] = []
    for node in element.iter():
        if node.tag == _w("t", namespace):
            parts.append(node.text or "")
        elif node.tag in {
            _w("tab", namespace),
            _w("br", namespace),
            _w("cr", namespace),
        }:
            parts.append("\t" if node.tag == _w("tab", namespace) else "\n")
    return "".join(parts).strip()


def _table_text(element: ElementTree.Element, namespace: str = _WORD_NS) -> str:
    """按行列提取 DOCX 表格，避免合同表格内容静默丢失。"""

    rows: list[str] = []
    for row in element.findall(_w("tr", namespace)):
        cells: list[str] = []
        for cell in row.findall(_w("tc", namespace)):
            paragraphs = [
                value
                for value in (
                    _paragraph_text(p, namespace)
                    for p in cell.findall(f".//{_w('p', namespace)}")
                )
                if value
            ]
            cells.append("\n".join(paragraphs))
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


class ContractDocumentParser:
    """按扩展名选择 PDF、DOCX 或 DOC 解析器。"""

    def __init__(self, *, doc_command: str = "antiword", doc_timeout: float = 30.0) -> None:
        self.pdf_parser = ContractPDFParser()
        self.doc_command = doc_command
        self.doc_timeout = doc_timeout

    def inspect(self, path: str | Path) -> PDFInspection:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == PDF_SUFFIX:
            return self.pdf_parser.inspect(path)
        if suffix == DOCX_SUFFIX:
            return self._inspect_docx(path)
        if suffix == DOC_SUFFIX:
            return self._inspect_doc(path)
        raise DocumentParseError("暂不支持该合同文件格式，请上传 PDF、DOC 或 DOCX")

    def supports_ocr(self, path: str | Path) -> bool:
        """只有 PDF 能渲染页面图片交给 OCR；Word 文字提取不重复走 OCR。"""

        return Path(path).suffix.lower() == PDF_SUFFIX

    def render_page(self, path: str | Path, page_no: int, *, dpi: int = 200) -> bytes:
        if not self.supports_ocr(path):
            raise DocumentParseError("Word 文档当前没有可供 OCR 的页面渲染路径")
        return self.pdf_parser.render_page(path, page_no, dpi=dpi)

    def _inspect_docx(self, path: Path) -> PDFInspection:
        try:
            with zipfile.ZipFile(path) as archive:
                document_info = archive.getinfo("word/document.xml")
                if document_info.file_size > MAX_DOCX_XML_BYTES:
                    raise DocumentParseError("DOCX 正文超过安全解析大小限制")
                document_xml = archive.read(document_info)
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
            raise DocumentParseError("DOCX 文件无法读取或已损坏") from error

        try:
            root = ElementTree.fromstring(document_xml)
        except Exception as error:
            raise DocumentParseError("DOCX 文档结构无法读取") from error

        namespace = _namespace_from_tag(root.tag)
        if namespace not in _SUPPORTED_WORD_NAMESPACES:
            raise DocumentParseError("DOCX 使用了不支持的 OOXML 命名空间")

        body = root.find(f".//{_w('body', namespace)}")
        if body is None:
            raise DocumentParseError("DOCX 文档没有正文内容")

        blocks: list[str] = []
        for child in list(body):
            if child.tag == _w("p", namespace):
                value = _paragraph_text(child, namespace)
            elif child.tag == _w("tbl", namespace):
                value = _table_text(child, namespace)
            else:
                continue
            if value:
                blocks.append(value)

        text = "\n".join(blocks).strip()
        flags = ["format_page_boundary_unavailable"]
        mode = "native" if text else "scanned"
        if not text:
            flags.extend(["no_native_text", "ocr_not_supported"])

        return PDFInspection(
            page_count=1,
            pages=(
                PDFPageInspection(
                    page_no=1,
                    mode=mode,
                    text=text,
                    image_area_ratio=0.0,
                    quality_flags=tuple(flags),
                ),
            ),
        )

    def _inspect_doc(self, path: Path) -> PDFInspection:
        command = shutil.which(self.doc_command) or self.doc_command
        try:
            completed = subprocess.run(
                [command, "-m", "UTF-8", str(path)],
                check=True,
                capture_output=True,
                timeout=self.doc_timeout,
            )
        except FileNotFoundError as error:
            raise DocumentParseError(
                "服务器未安装 DOC 解析组件，请先安装 antiword 或另存为 PDF"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise DocumentParseError("DOC 文件解析超时，请另存为 PDF 后重试") from error
        except subprocess.CalledProcessError as error:
            raise DocumentParseError("DOC 文件无法读取，请另存为 PDF 后重试") from error

        text = completed.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        raw_pages = text.split("\f")
        while len(raw_pages) > 1 and not raw_pages[-1].strip():
            raw_pages.pop()
        if not raw_pages:
            raw_pages = [""]

        pages: list[PDFPageInspection] = []
        for page_no, raw_text in enumerate(raw_pages, start=1):
            page_text = raw_text.strip()
            flags = ["format_page_boundary_unavailable"]
            mode = "native" if page_text else "scanned"
            if not page_text:
                flags.extend(["no_native_text", "ocr_not_supported"])
            pages.append(
                PDFPageInspection(
                    page_no=page_no,
                    mode=mode,
                    text=page_text,
                    image_area_ratio=0.0,
                    quality_flags=tuple(flags),
                )
            )

        return PDFInspection(page_count=len(pages), pages=tuple(pages))
