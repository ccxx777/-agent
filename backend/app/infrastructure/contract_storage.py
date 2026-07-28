"""用户合同的私有文件存储。

文件名不直接拼接用户输入，而是按 review UUID 生成目录和固定文件名，避免
路径穿越。原始 PDF/DOC/DOCX 不进入 Git、公共 data_worker 或 Qdrant。
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path


class ContractStorageError(ValueError):
    """合同文件保存或读取失败。"""


class PrivateContractStorage:
    """将原始合同保存到应用私有目录。"""

    SUPPORTED_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _directory(self, review_id: str) -> Path:
        try:
            safe_id = str(uuid.UUID(review_id))
        except ValueError as error:
            raise ContractStorageError("无效的 review_id") from error
        return self.root / safe_id

    def save(self, review_id: str, content: bytes, *, suffix: str = ".pdf") -> Path:
        if not content:
            raise ContractStorageError("合同文件为空")
        safe_suffix = suffix.lower()
        if safe_suffix not in self.SUPPORTED_SUFFIXES:
            raise ContractStorageError("不支持的合同文件格式")

        directory = self._directory(review_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"original{safe_suffix}"
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=directory, prefix=".upload-", suffix=".tmp", delete=False
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = temporary.name
            os.replace(temporary_path, target)
        except OSError as error:
            if temporary_path:
                Path(temporary_path).unlink(missing_ok=True)
            raise ContractStorageError("合同文件保存失败") from error
        return target

    def path_for(self, review_id: str) -> Path:
        directory = self._directory(review_id)
        for suffix in (".pdf", ".docx", ".doc"):
            path = directory / f"original{suffix}"
            if path.is_file():
                return path
        raise ContractStorageError("合同文件不存在")

    def delete(self, review_id: str) -> None:
        directory = self._directory(review_id)
        if not directory.exists():
            return
        for child in directory.iterdir():
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
        directory.rmdir()
