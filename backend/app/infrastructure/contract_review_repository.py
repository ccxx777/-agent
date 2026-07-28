"""合同审查任务的 PostgreSQL 持久化边界。"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


class ContractReviewRepository:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def create_task(self, record: dict[str, Any]) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                    INSERT INTO contract_review_tasks
                        (review_id, user_id, filename, content_type, size_bytes,
                         sha256, storage_path, page_count, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                    """,
                (
                    record["review_id"],
                    record["user_id"],
                    record["filename"],
                    record["content_type"],
                    record["size_bytes"],
                    record["sha256"],
                    record["storage_path"],
                    record.get("page_count"),
                ),
            )

    async def mark_status(self, review_id: str, status: str, error_message: str | None = None) -> None:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                    UPDATE contract_review_tasks
                    SET status = %s, error_message = %s, updated_at = NOW()
                    WHERE review_id = %s
                    """,
                (status, error_message, review_id),
            )

    async def save_result(
        self,
        review_id: str,
        *,
        status: str,
        quality: dict[str, Any],
        privacy: dict[str, Any],
        pages: list[dict[str, Any]],
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
                await cur.execute(
                    """
                        UPDATE contract_review_tasks
                        SET status = %s,
                            quality = %s,
                            privacy = %s,
                            error_message = NULL,
                            updated_at = NOW()
                        WHERE review_id = %s
                        """,
                    (status, Jsonb(quality), Jsonb(privacy), review_id),
                )
                await cur.execute(
                    "DELETE FROM contract_review_pages WHERE review_id = %s",
                    (review_id,),
                )
                for page in pages:
                    await cur.execute(
                        """
                            INSERT INTO contract_review_pages
                                (review_id, page_no, mode, redacted_text,
                                 ocr_used, quality_flags)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                        (
                            review_id,
                            page["page_no"],
                            page["mode"],
                            page["text"],
                            page["ocr_used"],
                            page["quality_flags"],
                        ),
                    )

    async def get_task(self, review_id: str, user_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT review_id, user_id, filename, content_type, size_bytes,
                           sha256, storage_path, status, page_count, quality,
                           privacy, error_message, created_at, updated_at
                    FROM contract_review_tasks
                    WHERE review_id = %s AND user_id = %s
                    """,
                    (review_id, user_id),
                )
                task = await cur.fetchone()
                if task is None:
                    return None
                await cur.execute(
                    """
                    SELECT page_no, mode, redacted_text AS text,
                           ocr_used, quality_flags
                    FROM contract_review_pages
                    WHERE review_id = %s
                    ORDER BY page_no
                    """,
                    (review_id,),
                )
                task["pages"] = await cur.fetchall()
                return dict(task)

    async def list_pending(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT review_id, status, storage_path
                    FROM contract_review_tasks
                    WHERE status IN ('queued', 'extracting')
                    ORDER BY created_at
                    """
                )
                return [dict(row) for row in await cur.fetchall()]
