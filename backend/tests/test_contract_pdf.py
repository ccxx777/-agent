"""PyMuPDF 合同页面分类测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz
from app.infrastructure.contract_pdf import ContractPDFParser


class ContractPDFParserTests(unittest.TestCase):
    def test_classifies_native_text_page(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text(
                (72, 72),
                "Both parties sign this employment contract. The term, workplace, and salary are defined in the clauses below.",
            )
            document.save(path)
            document.close()

            inspection = ContractPDFParser().inspect(path)

        self.assertEqual(inspection.page_count, 1)
        self.assertEqual(inspection.pages[0].mode, "native")
        self.assertIn("employment contract", inspection.pages[0].text)

    def test_renders_page_as_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "render.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "劳动合同")
            document.save(path)
            document.close()

            image_bytes = ContractPDFParser().render_page(path, 1)

        self.assertTrue(image_bytes.startswith(b"\xff\xd8\xff"))


if __name__ == "__main__":
    unittest.main()
