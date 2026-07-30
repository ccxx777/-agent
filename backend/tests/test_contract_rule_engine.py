from __future__ import annotations

import unittest

from app.schemas.contract_extraction import ContractEvidence
from app.services.contract_rule_engine import ContractRuleEngine


class ContractRuleEngineTests(unittest.TestCase):
    def test_social_insurance_waiver_is_high_risk_prompt(self):
        evidence = ContractEvidence(
            page_no=1,
            quote="乙方自愿放弃社会保险",
            char_start=0,
            char_end=10,
        )
        findings = ContractRuleEngine().evaluate(
            [
                {
                    "fact_id": "fact_social",
                    "category": "social_insurance",
                    "name": "社会保险",
                    "value": "乙方自愿放弃社会保险，由员工自行承担",
                    "evidence": [evidence],
                }
            ]
        )

        social = next(item for item in findings if item.rule_id == "LC-010")
        self.assertEqual(social.risk_level.value, "high")
        self.assertEqual(social.fact_ids, ["fact_social"])
        self.assertEqual(social.evidence[0].quote, "乙方自愿放弃社会保险")

    def test_core_missing_facts_are_unconfirmed_not_high(self):
        findings = ContractRuleEngine().evaluate([])

        missing = {item.rule_id: item for item in findings}
        self.assertIn("LC-001", missing)
        self.assertIn("LC-006", missing)
        self.assertEqual(missing["LC-001"].risk_level.value, "unconfirmed")
        self.assertNotEqual(missing["LC-001"].risk_level.value, "high")

    def test_rule_catalog_is_versionable_and_queries_are_deduplicated(self):
        engine = ContractRuleEngine()
        self.assertGreaterEqual(len(engine.rules), 15)
        findings = engine.evaluate(
            [
                {
                    "fact_id": "fact_social",
                    "category": "社保",
                    "name": "社会保险",
                    "value": "不缴纳社保",
                    "evidence": [],
                }
            ]
        )
        queries = engine.queries_for_findings(findings)
        self.assertEqual(len(queries), len({rule_id for rule_id, _ in queries}))


if __name__ == "__main__":
    unittest.main()
