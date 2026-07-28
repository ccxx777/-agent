"""合同文本的本地隐私脱敏。

这个模块只处理已经得到的文本，不负责识别 PDF 或调用模型。它在进入
Embedding、Reranker、LLM、日志和评测之前运行，只返回脱敏后的文本和不含
敏感值的统计信息。原始合同仍由私有文件存储保留，不在这里输出。

匹配时使用一个临时的 detection view：移除 Unicode ``Cf`` 格式字符（例如
零宽空格、BOM、双向控制符），并做逐字符 NFKC 归一化。替换时再通过索引
映射回原文本，所以普通文字不会因为清理检测字符而被改写。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_INVISIBLE_EXTRA = {"\u00ad"}  # soft hyphen
_ID_18 = re.compile(r"(?<!\d)([1-9]\d{5})\d{8}([\dXx]{4})(?!\d)")
_ID_15 = re.compile(r"(?<!\d)([1-9]\d{5})\d{6}(\d{3})(?!\d)")
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)[ -]?(\d{4})[ -]?(\d{4})(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){16,19}(?!\d)")


@dataclass(frozen=True)
class RedactionMatch:
    """一次脱敏命中的内部位置；不会被写入 API 响应或日志。"""

    category: str
    start: int
    end: int
    replacement: str


@dataclass(frozen=True)
class RedactionResult:
    """脱敏结果及可安全记录的统计信息。"""

    text: str
    counts: dict[str, int]
    invisible_sequences_detected: int
    matches: tuple[RedactionMatch, ...]

    @property
    def changed(self) -> bool:
        """返回文本是否发生过脱敏替换。"""

        return bool(self.matches)


@dataclass(frozen=True)
class _DetectionView:
    text: str
    source_indices: tuple[int, ...]
    invisible_sequences_detected: int


def _is_invisible_format_char(char: str) -> bool:
    """判断常见零宽/方向控制字符，但不吞掉换行和制表符。"""

    return unicodedata.category(char) == "Cf" or char in _INVISIBLE_EXTRA


def _build_detection_view(text: str) -> _DetectionView:
    chars: list[str] = []
    source_indices: list[int] = []
    invisible_count = 0

    for source_index, char in enumerate(text):
        if _is_invisible_format_char(char):
            invisible_count += 1
            continue

        normalized = unicodedata.normalize("NFKC", char)
        for normalized_char in normalized:
            chars.append(normalized_char)
            source_indices.append(source_index)

    return _DetectionView(
        text="".join(chars),
        source_indices=tuple(source_indices),
        invisible_sequences_detected=invisible_count,
    )


def clean_invisible_chars(text: str) -> str:
    """返回移除常见零宽/格式字符的检测副本。

    该函数用于测试和检测，不应直接替换用户最终看到的原始合同文本。
    """

    return "".join(char for char in text if not _is_invisible_format_char(char))


def _span_in_source(view: _DetectionView, start: int, end: int) -> tuple[int, int]:
    """把 detection view 的半开区间映射回原文半开区间。"""

    source_start = view.source_indices[start]
    source_end = view.source_indices[end - 1] + 1
    return source_start, source_end


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit() or char in "Xx")


def _candidate_matches(view: _DetectionView) -> list[RedactionMatch]:
    candidates: list[RedactionMatch] = []

    for match in _ID_18.finditer(view.text):
        start, end = _span_in_source(view, match.start(), match.end())
        value = _digits(match.group(0))
        candidates.append(
            RedactionMatch("id_card", start, end, f"{value[:6]}{'*' * 8}{value[-4:]}")
        )

    for match in _ID_15.finditer(view.text):
        start, end = _span_in_source(view, match.start(), match.end())
        value = _digits(match.group(0))
        candidates.append(
            RedactionMatch("id_card", start, end, f"{value[:6]}{'*' * 6}{value[-3:]}")
        )

    for match in _PHONE.finditer(view.text):
        start, end = _span_in_source(view, match.start(), match.end())
        value = _digits(match.group(0))
        candidates.append(
            RedactionMatch("phone", start, end, f"{value[:3]}****{value[-4:]}")
        )

    for match in _CARD.finditer(view.text):
        value = _digits(match.group(0))
        if not 16 <= len(value) <= 19:
            continue
        start, end = _span_in_source(view, match.start(), match.end())
        candidates.append(
            RedactionMatch("bank_card", start, end, f"{value[:4]}****{value[-4:]}")
        )

    # 身份证优先于手机号/银行卡号；重叠候选只保留第一个命中。
    priority = {"id_card": 0, "phone": 1, "bank_card": 2}
    candidates.sort(
        key=lambda item: (item.start, priority[item.category], -(item.end - item.start))
    )
    accepted: list[RedactionMatch] = []
    for candidate in candidates:
        if any(candidate.start < item.end and item.start < candidate.end for item in accepted):
            continue
        accepted.append(candidate)
    return sorted(accepted, key=lambda item: item.start)


def desensitize_text(text: str) -> RedactionResult:
    """脱敏身份证号、手机号和银行卡号，其他文本保持原样。

    身份证号首版默认保留前 6 位和后 4 位；15 位旧式身份证保留前 6 位和
    后 3 位。零宽字符只在敏感命中范围内随替换一起移除，普通文本中的
    不可见字符不会被无差别改写。
    """

    view = _build_detection_view(text)
    matches = _candidate_matches(view)
    result = text
    for match in reversed(matches):
        result = result[: match.start] + match.replacement + result[match.end :]

    counts = {"id_card": 0, "phone": 0, "bank_card": 0}
    for match in matches:
        counts[match.category] += 1

    return RedactionResult(
        text=result,
        counts=counts,
        invisible_sequences_detected=view.invisible_sequences_detected,
        matches=tuple(matches),
    )
