"""劳动合同规则卡片与确定性风险检查。

规则引擎只消费已经过证据定位、并经过用户确认的事实快照。它不重新读取
原始文件，也不调用 LLM；每条命中都携带 fact_id、合同证据和后续检索查询，
这样可以把“合同写了什么”和“法律资料怎么解释”分成两条可审计链路。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.schemas.contract_review_workflow import (
    FindingType,
    RiskLevel,
    RuleFinding,
)


@dataclass(frozen=True)
class LaborRule:
    """一张可版本化的首版规则卡片。"""

    rule_id: str
    title: str
    aliases: tuple[str, ...]
    legal_references: tuple[str, ...]
    query: str
    recommendation: str
    required_for_review: bool = False


LABOR_RULES: tuple[LaborRule, ...] = (
    LaborRule(
        "LC-001",
        "合同主体与必备事项",
        ("parties", "party", "甲方", "乙方", "用人单位", "劳动者"),
        ("劳动合同法第十七条",),
        "劳动合同应载明哪些主体和必备事项",
        "补充或确认双方主体、岗位、工作地点、工作时间、报酬及社会保险等必备信息。",
        True,
    ),
    LaborRule(
        "LC-002",
        "书面劳动合同与建立关系时间",
        ("written_contract", "contract_date", "签订日期", "入职日期", "建立劳动关系"),
        ("劳动合同法第七条、第十条", "实施条例第六条、第七条"),
        "未及时订立书面劳动合同的法律后果",
        "确认签订日期、实际入职日期和是否存在续签或补签情况，并保留相关证据。",
        True,
    ),
    LaborRule(
        "LC-003",
        "合同期限",
        ("term", "contract_term", "合同期限", "起始日期", "终止日期", "固定期限", "无固定期限"),
        ("劳动合同法第十二条至第十四条",),
        "劳动合同期限和期限类型",
        "补充合同期限类型、起止日期及续签安排，避免仅写“长期有效”等模糊表述。",
        True,
    ),
    LaborRule(
        "LC-004",
        "试用期与合同期限匹配",
        ("probation", "试用期"),
        ("劳动合同法第十九条",),
        "试用期的最长期间和约定限制",
        "核对试用期长度、合同期限和是否重复约定；必要时补充具体起止日期。",
    ),
    LaborRule(
        "LC-005",
        "试用期工资",
        ("probation_salary", "试用期工资", "试用期薪资"),
        ("劳动合同法第二十条", "实施条例第十五条"),
        "试用期工资的最低保护标准",
        "同时确认转正工资、试用期工资和当地最低工资适用信息。",
    ),
    LaborRule(
        "LC-006",
        "工资与支付方式",
        ("salary", "wage", "compensation", "工资", "薪酬", "月工资", "基本工资", "劳动报酬"),
        ("劳动合同法第三十条", "劳动法第五十条"),
        "劳动报酬约定与工资支付",
        "补充金额、支付周期、发薪日、构成和绩效计算口径，避免只写“按公司制度执行”。",
        True,
    ),
    LaborRule(
        "LC-007",
        "工作内容与岗位",
        ("work_content", "job", "岗位", "工作内容", "职务"),
        ("劳动合同法第十七条",),
        "劳动合同工作内容和岗位约定",
        "尽量把岗位职责、汇报关系和重大调整条件写清楚，减少后续事实争议。",
    ),
    LaborRule(
        "LC-008",
        "工作地点",
        ("work_location", "location", "工作地点", "工作地"),
        ("劳动合同法第十七条", "实施条例第十四条"),
        "劳动合同工作地点及变更安排",
        "确认工作地点、异地调动条件和通勤/搬迁安排，不要只引用注册地或笼统区域。",
    ),
    LaborRule(
        "LC-009",
        "工作时间、休息休假和加班",
        ("work_hours", "hours", "工作时间", "工时", "加班", "休息", "休假"),
        ("劳动法第三十六条、第三十八条、第四十一条、第四十四条",),
        "工作时间、加班审批与加班工资",
        "确认工时制度、休息日、加班审批、调休及加班工资计算方式。",
        True,
    ),
    LaborRule(
        "LC-010",
        "社会保险",
        ("social_insurance", "social security", "社保", "社会保险", "五险"),
        ("社会保险法第四条、第五十八条、第六十条", "劳动合同法第十七条", "劳动争议解释（二）第十九条"),
        "劳动合同社会保险约定和放弃缴纳条款",
        "确认参保地、参保险种和缴费主体；不要以员工签字替代法定参保义务。",
        True,
    ),
    LaborRule(
        "LC-011",
        "劳动合同变更",
        ("modification", "change", "变更", "调整工资", "单方调整"),
        ("劳动合同法第三十五条",),
        "劳动合同变更是否需要协商一致",
        "把可调整事项、触发条件和书面确认方式写清楚，避免单方无限授权。",
    ),
    LaborRule(
        "LC-012",
        "解除、终止与通知",
        ("termination", "dismissal", "解除", "终止", "辞职", "解雇"),
        ("劳动合同法第三十六条、第三十九条、第四十条、第四十二条、第四十四条",),
        "劳动合同解除终止的法定条件和通知",
        "区分协商解除、员工辞职和单位解除，并补充通知、证明和交接安排。",
        True,
    ),
    LaborRule(
        "LC-013",
        "经济补偿与违法解除后果",
        ("compensation_after_termination", "经济补偿", "赔偿", "违法解除"),
        ("劳动合同法第四十六条至第四十八条、第八十七条",),
        "劳动合同解除终止时的经济补偿和赔偿",
        "保留工资、工作年限和解除原因证据，并由专业人员核对计算口径。",
    ),
    LaborRule(
        "LC-014",
        "培训服务期与违约金",
        ("training", "service_period", "培训", "服务期", "培训费"),
        ("劳动合同法第二十二条、第二十五条", "实施条例第十六条、第十七条"),
        "专项培训服务期和违约金范围",
        "明确培训项目、实际费用、服务期限和违约金计算依据，保留费用凭证。",
    ),
    LaborRule(
        "LC-015",
        "竞业限制",
        ("non_compete", "non-compete", "竞业", "竞业限制"),
        ("劳动合同法第二十三条、第二十四条", "劳动争议解释（一）第三十六条至第四十条"),
        "竞业限制人员范围、期限和补偿",
        "确认适用人员、期限、地域、补偿标准和解除方式，不要只写员工单方义务。",
    ),
    LaborRule(
        "LC-016",
        "保密、知识产权与成果归属",
        ("confidentiality", "intellectual_property", "保密", "知识产权", "成果归属"),
        ("劳动合同法第二十三条",),
        "保密义务和职务成果归属约定",
        "区分工作期间和离职后的义务，明确保密信息范围、期限和合理补偿。",
    ),
    LaborRule(
        "LC-017",
        "争议解决与证据",
        ("dispute", "dispute_resolution", "争议", "仲裁", "诉讼"),
        ("劳动争议调解仲裁法第二条、第五条、第六条、第二十七条",),
        "劳动争议处理路径和举证责任",
        "保留考勤、工资、通知、沟通和规章制度送达证据；不要以合同条款排除法定救济。",
    ),
)

_MISSING_TEXT = re.compile(r"缺失|未提供|未知|待补充|面议|待定|空白|无")
_WAIVE_SOCIAL = re.compile(r"不缴|不买|放弃.*社保|自愿放弃.*社会保险|无需.*缴纳|全部由.*承担")
_UNILATERAL_TERMINATION = re.compile(r"随时解除|任意解除|无理由解除|单方决定|无需通知")
_UNILATERAL_CHANGE = re.compile(r"单方.*调整|随意.*变更|可随时修改|以公司制度为准")
_OVERTIME_RISK = re.compile(r"不支付.*加班|无偿加班|无限制加班|加班费.*包含|不另行支付")
_PROBATION_TOO_LONG = re.compile(r"试用期[^\n]{0,20}(?:超过|大于|长达)\s*[六6七7八8九9一二三四五]\s*个?月")


class ContractRuleEngine:
    """将事实快照转换为规则命中；不会把缺证据事实升级为高风险。"""

    def __init__(self, rules: tuple[LaborRule, ...] = LABOR_RULES) -> None:
        self.rules = rules
        self._by_id = {rule.rule_id: rule for rule in rules}

    def evaluate(self, facts: list[Mapping[str, Any]]) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for rule in self.rules:
            matched = self._matching_facts(rule, facts)
            if not matched:
                if rule.required_for_review:
                    findings.append(
                        self._missing_finding(rule)
                    )
                continue
            finding = self._evaluate_match(rule, matched)
            if finding is not None:
                findings.append(finding)
        return findings

    def rules_for_findings(self, findings: list[RuleFinding]) -> list[LaborRule]:
        return [self._by_id[finding.rule_id] for finding in findings if finding.rule_id in self._by_id]

    def queries_for_findings(self, findings: list[RuleFinding]) -> list[tuple[str, str]]:
        """返回去重后的 ``(rule_id, query)``，供法律检索节点调用。"""

        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for rule in self.rules_for_findings(findings):
            if rule.rule_id in seen:
                continue
            seen.add(rule.rule_id)
            result.append((rule.rule_id, rule.query))
        return result

    @staticmethod
    def _matching_facts(rule: LaborRule, facts: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        aliases = tuple(alias.lower() for alias in rule.aliases)
        matched: list[Mapping[str, Any]] = []
        for fact in facts:
            haystack = " ".join(
                str(fact.get(key) or "")
                for key in ("category", "name")
            ).lower()
            if any(alias in haystack for alias in aliases):
                matched.append(fact)
        return matched

    def _missing_finding(self, rule: LaborRule) -> RuleFinding:
        return RuleFinding(
            rule_id=rule.rule_id,
            title=rule.title,
            finding_type=FindingType.MISSING_INFORMATION,
            risk_level=RiskLevel.UNCONFIRMED,
            summary=f"尚未获得“{rule.title}”的已确认事实，当前无法完成该项判断。",
            legal_references=list(rule.legal_references),
            recommendation=rule.recommendation,
            question=f"请补充或确认与“{rule.title}”有关的合同事实。",
        )

    def _evaluate_match(
        self,
        rule: LaborRule,
        matched: list[Mapping[str, Any]],
    ) -> RuleFinding | None:
        value_text = "；".join(str(fact.get("value") or "") for fact in matched)
        fact_ids = [str(fact.get("fact_id")) for fact in matched if fact.get("fact_id")]
        evidence = [evidence for fact in matched for evidence in fact.get("evidence", [])]

        if rule.rule_id == "LC-010" and _WAIVE_SOCIAL.search(value_text):
            return self._finding(
                rule,
                FindingType.POSSIBLE_CONFLICT,
                RiskLevel.HIGH,
                "合同出现不缴纳、放弃或由员工自行承担社会保险的表述，需结合实际参保情况复核。",
                fact_ids,
                evidence,
                question="请确认实际参保主体、参保地和已发生的缴费情况。",
            )
        if rule.rule_id == "LC-012" and _UNILATERAL_TERMINATION.search(value_text):
            return self._finding(
                rule,
                FindingType.POSSIBLE_CONFLICT,
                RiskLevel.HIGH,
                "解除或终止条款包含单方、无理由或无需通知的宽泛表述，可能与法定条件不一致。",
                fact_ids,
                evidence,
            )
        if rule.rule_id == "LC-011" and _UNILATERAL_CHANGE.search(value_text):
            return self._finding(
                rule,
                FindingType.POSSIBLE_CONFLICT,
                RiskLevel.MEDIUM,
                "合同允许单方或随时调整重要条件，具体效力需要结合调整内容和实际履行判断。",
                fact_ids,
                evidence,
            )
        if rule.rule_id == "LC-009" and _OVERTIME_RISK.search(value_text):
            return self._finding(
                rule,
                FindingType.POSSIBLE_CONFLICT,
                RiskLevel.HIGH,
                "工作时间或加班条款出现不支付、无偿或无限制加班表述，需核对实际考勤和支付记录。",
                fact_ids,
                evidence,
            )
        if rule.rule_id == "LC-004" and _PROBATION_TOO_LONG.search(value_text):
            return self._finding(
                rule,
                FindingType.POSSIBLE_CONFLICT,
                RiskLevel.MEDIUM,
                "试用期表述看起来可能超过法定期限，需要根据合同期限和实际日期复核。",
                fact_ids,
                evidence,
            )
        if rule.rule_id == "LC-006" and _MISSING_TEXT.search(value_text):
            return self._finding(
                rule,
                FindingType.MISSING_INFORMATION,
                RiskLevel.UNCONFIRMED,
                "工资金额或支付口径不明确，当前不能可靠计算劳动报酬。",
                fact_ids,
                evidence,
            )
        if rule.rule_id == "LC-015" and value_text.strip():
            return self._finding(
                rule,
                FindingType.OBSERVATION,
                RiskLevel.MEDIUM,
                "合同包含竞业限制安排，应进一步核对适用人员、期限、地域和竞业补偿。",
                fact_ids,
                evidence,
            )
        if rule.rule_id == "LC-014" and "违约金" in value_text:
            return self._finding(
                rule,
                FindingType.POSSIBLE_CONFLICT,
                RiskLevel.MEDIUM,
                "合同包含培训/服务期违约金表述，应核对培训费用和约定范围。",
                fact_ids,
                evidence,
            )
        return None

    @staticmethod
    def _finding(
        rule: LaborRule,
        finding_type: FindingType,
        risk_level: RiskLevel,
        summary: str,
        fact_ids: list[str],
        evidence: list[Any],
        *,
        question: str | None = None,
    ) -> RuleFinding:
        return RuleFinding(
            rule_id=rule.rule_id,
            title=rule.title,
            finding_type=finding_type,
            risk_level=risk_level,
            summary=summary,
            fact_ids=fact_ids,
            legal_references=list(rule.legal_references),
            evidence=evidence,
            recommendation=rule.recommendation,
            question=question,
        )
