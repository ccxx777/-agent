"""文档 SHA256 与 PostgreSQL 元数据仓储。

SHA256 用于判断内容是否已入库，``source`` 只保存文件路径。数据库连接池按
第一次实际入库请求懒加载，导入模块不会连接 PostgreSQL。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_sha256(file_path: Path) -> str:
    """流式计算文件 SHA256，避免把大文件一次读入内存。"""
    digest = hashlib.sha256()
    with file_path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FingerprintRepository:
    """封装 ``rag_documents`` 的指纹查询和元数据写入。"""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    def _get_pool(self):
        """首次使用时创建小型同步连接池。"""
        if self._pool is None:
            import psycopg_pool

            self._pool = psycopg_pool.ConnectionPool(
                self._dsn,
                min_size=1,
                max_size=2,
                open=True,
            )
        return self._pool

    def find(self, sha256: str) -> tuple[bool, str | None]:
        """返回 ``(是否存在, 已记录路径)``，保留 source 为空的历史记录。"""
        with self._get_pool().connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT source FROM rag_documents WHERE sha256 = %s LIMIT 1",
                    (sha256,),
                )
                row = cursor.fetchone()
        return (True, str(row[0]) if row[0] is not None else None) if row else (False, None)

    def update_source(self, sha256: str, new_path: str) -> None:
        """文件内容未变但路径变化时，只同步 PostgreSQL 路径。"""
        with self._get_pool().connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE rag_documents SET source = %s, updated_at = NOW() WHERE sha256 = %s",
                    (new_path, sha256),
                )
        logger.info("PG path updated: %s → %s", sha256[:12], new_path)

    def record_document(
        self,
        *,
        doc_id: str,
        title: str,
        source: str,
        sha256: str,
        chunk_count: int,
    ) -> None:
        """Qdrant 写入成功后，幂等记录文档级元数据。"""
        with self._get_pool().connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO rag_documents
                        (doc_id, title, source, sha256, chunk_count, indexed_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (sha256) DO UPDATE SET
                        title = EXCLUDED.title,
                        source = EXCLUDED.source,
                        chunk_count = EXCLUDED.chunk_count,
                        indexed_at = NOW(),
                        updated_at = NOW()
                    """,
                    (doc_id, title, source, sha256, chunk_count),
                )
