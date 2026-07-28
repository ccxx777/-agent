"""合同隐私脱敏单元测试。"""

from __future__ import annotations

import unittest

from app.services.privacy_redaction import clean_invisible_chars, desensitize_text


class ContractPrivacyTests(unittest.TestCase):
    def test_masks_id_phone_and_bank_card_without_changing_other_text(self):
        original = (
            "乙方身份证号：110105199001011234，联系电话：13912345678。"
            "银行卡号：6222021234567890，请将款项汇入此账户。"
        )

        result = desensitize_text(original)

        self.assertEqual(
            result.text,
            "乙方身份证号：110105********1234，联系电话：139****5678。"
            "银行卡号：6222****7890，请将款项汇入此账户。",
        )
        self.assertEqual(result.counts, {"id_card": 1, "phone": 1, "bank_card": 1})

    def test_zero_width_characters_are_detected_without_global_text_rewrite(self):
        original = "普通词\u200b保持原样；身份证：110105\u200B1990\u200C01011234。"

        result = desensitize_text(original)

        self.assertEqual(result.text, "普通词\u200b保持原样；身份证：110105********1234。")
        self.assertGreaterEqual(result.invisible_sequences_detected, 3)
        self.assertEqual(clean_invisible_chars("A\u200BB"), "AB")

    def test_phone_and_card_with_common_ocr_separators_are_masked(self):
        result = desensitize_text("电话 139-1234-5678，卡号 6222 0212 3456 7890")

        self.assertEqual(result.text, "电话 139****5678，卡号 6222****7890")

    def test_unrelated_numbers_are_not_masked_as_phone_or_card(self):
        original = "合同编号：20260728，金额：123456.78，条款编号：第16条。"

        self.assertEqual(desensitize_text(original).text, original)


if __name__ == "__main__":
    unittest.main()

