"""文档分块器。

分块参数和分隔符完全沿用原 Sentinel：1000 字符、200 字符重叠，优先按段落、
换行和中文标点切分。该模块不调用模型，也不写数据库。
"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

SEPARATORS = ["\n\n", "\n", "。", "；", " ", ""]


class TextChunker:
    """对文本执行确定性的递归字符分块。"""

    def __init__(self, *, chunk_size: int, chunk_overlap: int) -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=SEPARATORS,
            length_function=len,
            is_separator_regex=False,
        )

    def split(self, text: str) -> list[str]:
        """返回保持原文顺序的非空文本块。"""
        return self._splitter.split_text(text)
