"""Data Worker写入Retrieval v2所需的稳定英文/中文BM25向量。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

import jieba


BM25_K1 = 1.2
BM25_B = 0.75
BM25_AVERAGE_DOCUMENT_LENGTH = 256.0
ENGLISH_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")


def english_tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in ENGLISH_TOKEN_PATTERN.finditer(text)]


def chinese_tokens(text: str) -> list[str]:
    return [token.strip().lower() for token in jieba.lcut(text, cut_all=False) if token.strip()]


def sparse_token_id(token: str) -> int:
    digest = hashlib.blake2s(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def bm25_document_sparse(tokens: list[str]) -> tuple[list[int], list[float]]:
    """生成BM25 TF部分；Collection的IDF modifier动态补充语料IDF。"""

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
            + BM25_B * document_length / BM25_AVERAGE_DOCUMENT_LENGTH
        )
        value = frequency * (BM25_K1 + 1.0) / denominator
        weighted.append((token_id, float(value)))
    weighted.sort()
    return [item[0] for item in weighted], [item[1] for item in weighted]
