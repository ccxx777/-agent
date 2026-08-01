"""合同审查报告 PDF 导出测试。"""

from __future__ import annotations

import unittest

from app.infrastructure.contract_report_pdf import render_contract_report_pdf


class ContractReportPdfTests(unittest.TestCase):
    def test_rendered_report_is_a_non_empty_pdf(self) -> None:
        payload = {
            "review_id": "review-1",
            "report_version": 1,
            "workflow_status": "completed",
            "scope": "labor_contract_national",
            "generated_at": "2026-08-01T00:00:00+00:00",
            "findings": [
                {
                    "title": "社会保险",
                    "risk_level": "high",
                    "summary": "合同出现可能排除法定义务的表述。",
                    "evidence": [{"quote": "乙方自愿放弃社会保险。"}],
                    "recommendation": "补充依法参保的约定。",
                }
            ],
            "pending_questions": ["请确认实际参保情况。"],
            "legal_sources": [
                {
                    "source_level": "A",
                    "title": "劳动合同法",
                    "quote": "用人单位和劳动者必须依法参加社会保险。",
                    "official_url": "https://flk.npc.gov.cn/",
                }
            ],
            "case_sources": [],
            "warnings": [],
            "disclaimer": "本报告仅供参考。",
        }

        result = render_contract_report_pdf(payload)

        self.assertTrue(result.startswith(b"%PDF-"))
        self.assertGreater(len(result), 1_000)
        self.assertIn(b"%%EOF", result)


if __name__ == "__main__":
    unittest.main()
