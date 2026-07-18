"""知识库源文件加载器。

当前项目只接收 UTF-8 Markdown/Text。Loader 只读取内容和规范化路径，不负责
分块、指纹或格式转换；未来增加 PDF 时可以新增独立实现而不改流水线编排。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoadedDocument:
    """Loader 输出的文档内容与来源信息。"""

    source: str
    content: str


def load_document(file_path: Path) -> LoadedDocument:
    """读取一个 UTF-8 文本文件；文件不存在时抛出 ``FileNotFoundError``。"""
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    return LoadedDocument(source=str(file_path), content=file_path.read_text(encoding="utf-8"))
