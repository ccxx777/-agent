"""中英文查询语言分析与动态融合权重计算。

``specificity``（S）统一限制在 ``[0.2, 0.8]``：S 越高越偏向字面检索，
``1-S`` 越高越偏向语义检索。英文使用功能词密度，中文使用社交/句法填充词
密度；两者都按命中词数除以总词数，避免旧版按词表种类数造成长度偏差。

语言检测和中文词性分词都延迟导入，使静态检查和不涉及检索的管理命令不会
加载额外模型或词典。生产依赖由 ``backend/requirements.txt`` 固定。
"""

from __future__ import annotations

import re
import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Literal


MIN_SPECIFICITY = 0.2
MAX_SPECIFICITY = 0.8
UNCERTAIN_SPECIFICITY = 0.5
LANGUAGE_CONFIDENCE_THRESHOLD = 0.7
BM25_K1 = 1.2
BM25_B = 0.75
BM25_AVERAGE_DOCUMENT_LENGTH = 256.0

# 来源为 NLTK English stopwords 的结构词子集。这里有意剔除 not/no/nor、
# very/too 等携带否定、程度或判断语义的词，避免削弱精确检索信号。
ENGLISH_FUNCTION_WORDS = frozenset(
    {
        "a", "an", "the",
        "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
        "you", "your", "yours", "yourself", "yourselves",
        "he", "him", "his", "himself", "she", "her", "hers", "herself",
        "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
        "this", "that", "these", "those",
        "am", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "doing", "have", "has", "had", "having",
        "can", "could", "may", "might", "must", "shall", "should", "will", "would",
        "of", "at", "by", "for", "with", "about", "against", "between", "into",
        "through", "during", "before", "after", "above", "below", "to", "from",
        "up", "down", "in", "out", "on", "off", "over", "under",
        "and", "but", "or", "because", "as", "until", "while", "if", "than",
        "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    }
)

CHINESE_SOCIAL_POS_PREFIXES = frozenset({"r", "u", "p", "c", "y"})
CHINESE_SOCIAL_PHRASES = (
    "请问", "我想", "我想问", "想请问", "能不能", "可不可以", "可以吗",
    "麻烦问一下", "麻烦帮我", "帮我看看", "我想了解", "请帮我", "请告诉我",
)
ENGLISH_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
CHINESE_CHARACTER_PATTERN = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class QuerySpecificity:
    """可记录到日志和评测结果中的查询分析结果。"""

    language: Literal["en", "zh", "fallback"]
    confidence: float
    total_tokens: int
    signal_tokens: int
    signal_density: float
    specificity: float

    @property
    def semantic_weight(self) -> float:
        return 1.0 - self.specificity

    @property
    def literal_weight(self) -> float:
        return self.specificity


def english_tokens(text: str) -> list[str]:
    """英文 ``word`` 风格分词；不做词形还原或停用词删除。"""

    return [match.group(0).lower() for match in ENGLISH_TOKEN_PATTERN.finditer(text)]


def chinese_tokens(text: str) -> list[str]:
    """Jieba精确模式分词，供中文BM25回退字段使用。"""

    try:
        import jieba
    except ImportError as error:  # pragma: no cover - 部署依赖缺失
        raise RuntimeError("中文查询分析需要安装 jieba") from error
    return [token.strip().lower() for token in jieba.lcut(text, cut_all=False) if token.strip()]


def detect_query_language(text: str) -> tuple[Literal["en", "zh", "fallback"], float]:
    """检测主语言；低置信度或极短文本返回中性兜底。"""

    normalized = text.strip()
    if len(normalized) <= 3:
        return "fallback", 0.0
    try:
        from langdetect import DetectorFactory, LangDetectException, detect_langs

        DetectorFactory.seed = 0
        candidates = detect_langs(normalized)
    except ImportError as error:  # pragma: no cover - 部署依赖缺失
        raise RuntimeError("查询语言检测需要安装 langdetect") from error
    except LangDetectException:
        return "fallback", 0.0
    if not candidates:
        return "fallback", 0.0
    best = candidates[0]
    confidence = float(best.prob)
    if confidence < LANGUAGE_CONFIDENCE_THRESHOLD:
        return "fallback", confidence
    language = str(best.lang).lower()
    if language in {"zh", "zh-cn", "zh-tw"} or language.startswith("zh-"):
        return "zh", confidence
    return "en", confidence


def _specificity_from_density(density: float) -> float:
    # 示例约束：density=0 -> 0.8；0.5 -> 0.5；0.6 -> 0.44。
    raw = MAX_SPECIFICITY - 0.6 * density
    return max(MIN_SPECIFICITY, min(MAX_SPECIFICITY, raw))


def _english_specificity(text: str, confidence: float) -> QuerySpecificity:
    tokens = english_tokens(text)
    signals = sum(token in ENGLISH_FUNCTION_WORDS for token in tokens)
    density = signals / len(tokens) if tokens else 0.0
    return QuerySpecificity(
        language="en",
        confidence=confidence,
        total_tokens=len(tokens),
        signal_tokens=signals,
        signal_density=density,
        specificity=_specificity_from_density(density),
    )


def _chinese_specificity(text: str, confidence: float) -> QuerySpecificity:
    try:
        import jieba.posseg as posseg
    except ImportError as error:  # pragma: no cover - 部署依赖缺失
        raise RuntimeError("中文查询分析需要安装 jieba") from error

    tagged = [(word.strip(), flag) for word, flag in posseg.cut(text) if word.strip()]
    pos_signals = sum(
        bool(flag) and flag[0] in CHINESE_SOCIAL_POS_PREFIXES for _, flag in tagged
    )
    # 短语白名单只补偿词性未计入的套话；同一短语按实际出现次数计数。
    phrase_signals = sum(text.count(phrase) for phrase in CHINESE_SOCIAL_PHRASES)
    signals = pos_signals + phrase_signals
    total = len(tagged) + phrase_signals
    density = min(1.0, signals / total) if total else 0.0
    return QuerySpecificity(
        language="zh",
        confidence=confidence,
        total_tokens=total,
        signal_tokens=signals,
        signal_density=density,
        specificity=_specificity_from_density(density),
    )


def calculate_query_specificity_details(query: str) -> QuerySpecificity:
    """计算中英文查询特征和统一S值。"""

    language, confidence = detect_query_language(query)
    if language == "fallback":
        return QuerySpecificity(
            language="fallback",
            confidence=confidence,
            total_tokens=0,
            signal_tokens=0,
            signal_density=0.5,
            specificity=UNCERTAIN_SPECIFICITY,
        )
    if language == "zh":
        return _chinese_specificity(query, confidence)
    return _english_specificity(query, confidence)


def calculate_query_specificity(query: str) -> float:
    """兼容旧调用方，只返回 ``S ∈ [0.2, 0.8]``。"""

    return calculate_query_specificity_details(query).specificity


def bm25_query_tokens(query: str, language: str) -> list[str]:
    """为命名BM25 Sparse Vector生成与离线建库一致的查询Token。"""

    if language == "zh":
        return chinese_tokens(query)
    return english_tokens(query)


def sparse_token_id(token: str) -> int:
    """把Token稳定映射到Qdrant Sparse Vector的u32索引。"""

    digest = hashlib.blake2s(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def bm25_document_sparse(
    tokens: list[str],
    *,
    average_document_length: float = BM25_AVERAGE_DOCUMENT_LENGTH,
) -> tuple[list[int], list[float]]:
    """生成不含IDF的BM25文档向量；IDF由Qdrant Sparse modifier动态应用。"""

    if not tokens:
        return [], []
    frequencies = Counter(tokens)
    document_length = len(tokens)
    weighted: list[tuple[int, float]] = []
    token_by_id: dict[int, str] = {}
    for token, frequency in frequencies.items():
        token_id = sparse_token_id(token)
        previous = token_by_id.setdefault(token_id, token)
        if previous != token:
            raise ValueError("BM25 Token u32哈希碰撞")
        denominator = frequency + BM25_K1 * (
            1.0
            - BM25_B
            + BM25_B * document_length / average_document_length
        )
        tf_weight = frequency * (BM25_K1 + 1.0) / denominator
        weighted.append((token_id, float(tf_weight)))
    weighted.sort()
    return [item[0] for item in weighted], [item[1] for item in weighted]
