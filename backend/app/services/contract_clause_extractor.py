"""合同条款切分与证据定位。

条款切分是确定性步骤，不依赖模型。模型只负责在已经切好的条款中抽取事实，
这样可以让每一个事实都回到脱敏页文本中进行本地定位，避免把模型生成的引用直接当成证据。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.schemas.contract_extraction import (
    ClauseType,
    ContractClause,
    ContractEvidence,
    EvidenceMatchType,
)


@dataclass(frozen=True)
class _Heading:
    title: str
    clause_type: ClauseType


_COMMON_HEADINGS: tuple[tuple[str, ClauseType], ...] = (
    ("当事人|甲乙双方|基本信息|双方信息", ClauseType.PARTIES),
    ("合同期限|劳动合同期限|履行期限|服务期限", ClauseType.TERM),
    ("试用期", ClauseType.PROBATION),
    ("工作内容|岗位职责|工作地点|工作场所", ClauseType.WORK_CONTENT),
    ("工作时间|休息休假|加班|工时", ClauseType.WORK_HOURS),
    ("劳动报酬|工资|薪酬|报酬", ClauseType.COMPENSATION),
    ("社会保险|住房公积金|五险一金", ClauseType.SOCIAL_INSURANCE),
    ("解除|终止|离职", ClauseType.TERMINATION),
    ("违约责任|赔偿责任|责任", ClauseType.LIABILITY),
    ("竞业限制|竞业禁止", ClauseType.NON_COMPETE),
    ("保密|知识产权|成果归属", ClauseType.CONFIDENTIALITY),
    ("争议解决|劳动争议|仲裁|管辖", ClauseType.DISPUTE_RESOLUTION),
)

_NUMBERED_HEADING = re.compile(
    r"^\s*(?:(?:第\s*[一二三四五六七八九十百千万零〇0-9]+\s*[章节条款])|"
    r"(?:[一二三四五六七八九十百千万零〇]+[、.．])|"
    r"(?:\d+(?:\.\d+)*[、.．:]?)|(?:[A-Z][、.．]))\s*(?P<title>.+?)\s*$",
    re.IGNORECASE,
)


def _classify_heading(title: str) -> ClauseType:
    for pattern, clause_type in _COMMON_HEADINGS:
        if re.search(pattern, title, flags=re.IGNORECASE):
            if clause_type is ClauseType.WORK_CONTENT and "地点" in title:
                return ClauseType.WORK_LOCATION
            if clause_type is ClauseType.CONFIDENTIALITY and "知识产权" in title:
                return ClauseType.INTELLECTUAL_PROPERTY
            return clause_type
    return ClauseType.OTHER


def _detect_heading(line: str) -> _Heading | None:
    stripped = re.sub(r"\s+", " ", line.strip())
    if not stripped or len(stripped) > 100:
        return None

    numbered = _NUMBERED_HEADING.match(stripped)
    if numbered:
        title = numbered.group("title").strip(" ：:、.．-")
        if title:
            return _Heading(title=title, clause_type=_classify_heading(title))

    # 无编号的常见标题通常是短行；要求不以句号结尾，减少把正文误切开的概率。
    if len(stripped) <= 36 and not re.search(r"[。！？.!?]$", stripped):
        clause_type = _classify_heading(stripped)
        if clause_type is not ClauseType.OTHER:
            return _Heading(title=stripped, clause_type=clause_type)
    return None


class ContractClauseSplitter:
    """把脱敏页文本切成带页码范围的条款块。"""

    def split(self, pages: Iterable[dict[str, Any]]) -> list[ContractClause]:
        current_parts: list[str] = []
        current_pages: set[int] = set()
        current_title = "合同正文"
        current_type = ClauseType.OTHER
        clauses: list[ContractClause] = []

        def flush() -> None:
            nonlocal current_parts, current_pages, current_title, current_type
            text = "".join(current_parts).strip()
            if not text or not current_pages:
                current_parts = []
                current_pages = set()
                return
            clause_no = len(clauses) + 1
            clauses.append(
                ContractClause(
                    clause_id=f"clause_{clause_no:03d}",
                    clause_type=current_type,
                    title=current_title,
                    text=text,
                    page_start=min(current_pages),
                    page_end=max(current_pages),
                    source_page_nos=sorted(current_pages),
                )
            )
            current_parts = []
            current_pages = set()

        for page in pages:
            page_no = int(page["page_no"])
            text = str(page.get("text") or "")
            if not text.strip():
                continue
            for line in text.splitlines(keepends=True):
                heading = _detect_heading(line)
                if heading is not None and current_parts and "".join(current_parts).strip():
                    flush()
                if heading is not None:
                    current_title = heading.title
                    current_type = heading.clause_type
                current_parts.append(line)
                current_pages.add(page_no)
        flush()
        return clauses


def _normalise_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalised_with_mapping(value: str) -> tuple[str, list[int]]:
    """折叠空白并保留规范化字符到原文字符的映射。"""

    output: list[str] = []
    mapping: list[int] = []
    pending_space = False
    pending_index = 0
    for index, char in enumerate(value):
        if char.isspace():
            if output:
                pending_space = True
                pending_index = index
            continue
        if pending_space:
            output.append(" ")
            mapping.append(pending_index)
            pending_space = False
        output.append(char)
        mapping.append(index)
    return "".join(output), mapping


class EvidenceLocator:
    """在脱敏页文本中重新定位模型提出的引用。"""

    def locate_quote(
        self,
        quote: str,
        pages: Iterable[dict[str, Any]],
        *,
        preferred_pages: set[int] | None = None,
        clause_id: str | None = None,
    ) -> ContractEvidence | None:
        candidate_pages = list(pages)
        if preferred_pages:
            scoped = [page for page in candidate_pages if int(page["page_no"]) in preferred_pages]
            if scoped:
                candidate_pages = scoped
        clean_quote = quote.strip()
        if not clean_quote:
            return None

        for page in candidate_pages:
            page_no = int(page["page_no"])
            text = str(page.get("text") or "")
            start = text.find(clean_quote)
            if start >= 0:
                return ContractEvidence(
                    page_no=page_no,
                    quote=text[start : start + len(clean_quote)],
                    char_start=start,
                    char_end=start + len(clean_quote),
                    match_type=EvidenceMatchType.EXACT,
                    clause_id=clause_id,
                )

        normalised_quote = _normalise_whitespace(clean_quote)
        if not normalised_quote:
            return None
        for page in candidate_pages:
            page_no = int(page["page_no"])
            text = str(page.get("text") or "")
            normalised_text, mapping = _normalised_with_mapping(text)
            start = normalised_text.find(normalised_quote)
            if start < 0 or start >= len(mapping):
                continue
            end_index = min(start + len(normalised_quote) - 1, len(mapping) - 1)
            char_start = mapping[start]
            char_end = mapping[end_index] + 1
            return ContractEvidence(
                page_no=page_no,
                quote=text[char_start:char_end],
                char_start=char_start,
                char_end=char_end,
                match_type=EvidenceMatchType.NORMALIZED,
                clause_id=clause_id,
            )
        return None

    def locate_fact(
        self,
        *,
        evidence_quotes: Iterable[str],
        value: Any,
        pages: list[dict[str, Any]],
        clauses: dict[str, ContractClause],
        clause_ids: Iterable[str],
    ) -> list[ContractEvidence]:
        clause_ids = list(clause_ids)
        preferred_pages: set[int] = set()
        for clause_id in clause_ids:
            clause = clauses.get(clause_id)
            if clause:
                preferred_pages.update(clause.source_page_nos)

        candidates = [str(quote) for quote in evidence_quotes if str(quote).strip()]
        if isinstance(value, str) and value.strip():
            candidates.append(value)
        evidence: list[ContractEvidence] = []
        seen: set[tuple[int, int, int]] = set()
        for candidate in candidates:
            located = self.locate_quote(
                candidate,
                pages,
                preferred_pages=preferred_pages or None,
                clause_id=next(iter(clause_ids), None),
            )
            if located is None:
                continue
            key = (located.page_no, located.char_start, located.char_end)
            if key not in seen:
                evidence.append(located)
                seen.add(key)
        return evidence
