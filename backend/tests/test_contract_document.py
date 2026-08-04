"""DOC/DOCX 合同文档解析测试。"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.infrastructure.contract_document import (
    ContractDocumentParser,
    DocumentParseError,
)


class ContractDocumentParserTests(unittest.TestCase):
    def test_extracts_docx_paragraphs_and_tables(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:r><w:t>劳务中介服务合同</w:t></w:r></w:p>
            <w:tbl>
              <w:tr><w:tc><w:p><w:r><w:t>甲方</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>乙方</w:t></w:r></w:p></w:tc></w:tr>
            </w:tbl>
          </w:body>
        </w:document>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)

            inspection = ContractDocumentParser().inspect(path)

        self.assertEqual(inspection.page_count, 1)
        self.assertEqual(inspection.pages[0].mode, "native")
        self.assertIn("劳务中介服务合同", inspection.pages[0].text)
        self.assertIn("甲方 | 乙方", inspection.pages[0].text)
        self.assertIn("format_page_boundary_unavailable", inspection.pages[0].quality_flags)

    def test_extracts_strict_ooxml_docx(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main">
          <w:body>
            <w:p><w:r><w:t>Strict OOXML paragraph</w:t></w:r></w:p>
            <w:tbl>
              <w:tr><w:tc><w:p><w:r><w:t>Strict A</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Strict B</w:t></w:r></w:p></w:tc></w:tr>
            </w:tbl>
          </w:body>
        </w:document>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strict-contract.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)

            inspection = ContractDocumentParser().inspect(path)

        self.assertEqual(inspection.pages[0].mode, "native")
        self.assertIn("Strict OOXML paragraph", inspection.pages[0].text)
        self.assertIn("Strict A | Strict B", inspection.pages[0].text)

    def test_extracts_legacy_doc_with_antiword(self):
        result = SimpleNamespace(stdout=b"first page\fsecond page\f")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.doc"
            path.write_bytes(b"legacy doc")
            with (
                patch("app.infrastructure.contract_document.shutil.which", return_value="antiword"),
                patch(
                    "app.infrastructure.contract_document.subprocess.run",
                    return_value=result,
                ) as run,
            ):
                inspection = ContractDocumentParser().inspect(path)

        self.assertEqual(inspection.page_count, 2)
        self.assertEqual(inspection.pages[0].text, "first page")
        self.assertEqual(inspection.pages[1].text, "second page")
        run.assert_called_once_with(
            ["antiword", "-m", "UTF-8", str(path)],
            check=True,
            capture_output=True,
            timeout=30.0,
        )

    def test_missing_antiword_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.doc"
            path.write_bytes(b"legacy doc")
            with (
                patch("app.infrastructure.contract_document.shutil.which", return_value=None),
                self.assertRaisesRegex(DocumentParseError, "antiword"),
            ):
                ContractDocumentParser().inspect(path)

    def test_word_formats_do_not_claim_ocr_page_rendering(self):
        parser = ContractDocumentParser()
        self.assertFalse(parser.supports_ocr("contract.doc"))
        self.assertFalse(parser.supports_ocr("contract.docx"))


if __name__ == "__main__":
    unittest.main()
