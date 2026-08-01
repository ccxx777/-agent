"""合同审查任务的 PostgreSQL 持久化边界。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


class ConfirmationRevisionConflict(RuntimeError):
    """事实确认提交基于过期 revision，调用方应重新读取表单。"""


class SessionOwnershipError(RuntimeError):
    """会话已属于其他用户，不能被合同任务接管。"""


class ContractReviewRepository:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def ensure_session(self, session_id: str, user_id: str) -> None:
        """幂等创建会话，并拒绝把已有会话转移给其他用户。"""

        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO sessions
                    (session_id, user_id, title, conversation_scope_version, has_contract_context)
                VALUES (%s, %s, %s, 1, FALSE)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id, user_id, "合同审查会话"),
            )
            await cur.execute(
                "SELECT user_id FROM sessions WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
            if row is None or str(row[0]) != str(user_id):
                raise SessionOwnershipError(session_id)

    async def get_session_owner(self, session_id: str) -> str | None:
        """Return the owner of a persisted session, or ``None`` when unknown."""

        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM sessions WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
            return str(row[0]) if row else None

    async def get_conversation_scope_state(
        self,
        session_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """读取统一会话的 scope 迁移状态；调用前已完成会话归属校验。"""

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT conversation_scope_version, has_contract_context
                FROM sessions
                WHERE session_id = %s AND user_id = %s
                """,
                (session_id, user_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def mark_conversation_scope_state(
        self,
        session_id: str,
        user_id: str,
        version: int,
    ) -> None:
        """持久化一次性 scope 迁移结果，避免每轮扫描 checkpoint。"""

        if version not in (1, 2):
            raise ValueError("conversation scope version must be 1 or 2")
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE sessions
                SET conversation_scope_version = %s,
                    updated_at = NOW()
                WHERE session_id = %s AND user_id = %s
                """,
                (version, session_id, user_id),
            )

    async def create_task(self, record: dict[str, Any]) -> None:
        if record.get("session_id"):
            await self.ensure_session(str(record["session_id"]), str(record["user_id"]))
        async with self._pool.connection() as conn, conn.cursor() as cur:
            if record.get("session_id"):
                await cur.execute(
                    """
                    UPDATE sessions
                    SET has_contract_context = TRUE, updated_at = NOW()
                    WHERE session_id = %s AND user_id = %s
                    """,
                    (record["session_id"], record["user_id"]),
                )
            await cur.execute(
                """
                    INSERT INTO contract_review_tasks
                        (review_id, user_id, session_id, filename, content_type, size_bytes,
                         sha256, storage_path, page_count, retention_policy, expires_at, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                    """,
                (
                    record["review_id"],
                    record["user_id"],
                    record.get("session_id"),
                    record["filename"],
                    record["content_type"],
                    record["size_bytes"],
                    record["sha256"],
                    record["storage_path"],
                    record.get("page_count"),
                    record.get("retention_policy", "short"),
                    record.get("expires_at"),
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

    async def mark_extraction_status(
        self,
        review_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> None:
        """更新事实提取状态；不覆盖文件解析错误信息。"""

        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                    UPDATE contract_review_tasks
                    SET extraction_status = %s,
                        extraction_result = COALESCE(%s, extraction_result),
                        updated_at = NOW()
                    WHERE review_id = %s
                    """,
                (status, Jsonb(result) if result is not None else None, review_id),
            )

    async def save_extraction(
        self,
        review_id: str,
        *,
        status: str,
        result: dict[str, Any],
    ) -> None:
        """原子保存条款、事实和证据定位结果。"""

        await self.mark_extraction_status(review_id, status, result=result)

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
                           session_id, sha256, storage_path, status, page_count, quality,
                           privacy, extraction_status, extraction_result,
                           retention_policy, expires_at, deleted_at,
                           confirmation_status, confirmation_revision,
                           confirmation_result, confirmed_at,
                           error_message, created_at, updated_at
                    FROM contract_review_tasks
                    WHERE review_id = %s AND user_id = %s
                      AND deleted_at IS NULL
                      AND COALESCE(expires_at, created_at + INTERVAL '7 days') > NOW()
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

    async def purge_expired(self) -> list[tuple[str, str]]:
        """列出到期任务但暂不删除，等待文件清理成功后再 finalize。"""

        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT review_id, storage_path
                FROM contract_review_tasks
                WHERE deleted_at IS NULL
                  AND COALESCE(expires_at, created_at + INTERVAL '7 days') <= NOW()
                ORDER BY expires_at NULLS FIRST, created_at
                """
            )
            return [(str(row[0]), str(row[1])) for row in await cur.fetchall()]

    async def finalize_expired(self, review_id: str) -> bool:
        """文件清理成功后删除到期任务及其级联数据。"""

        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM contract_review_tasks
                WHERE review_id = %s
                  AND deleted_at IS NULL
                  AND COALESCE(expires_at, created_at + INTERVAL '7 days') <= NOW()
                """,
                (review_id,),
            )
            return cur.rowcount > 0

    async def delete_task(self, review_id: str, user_id: str) -> str | None:
        """删除任务及其级联事实/报告，返回私有文件路径。"""

        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(
                """
                SELECT storage_path
                FROM contract_review_tasks
                WHERE review_id = %s AND user_id = %s AND deleted_at IS NULL
                FOR UPDATE
                """,
                (review_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            await cur.execute(
                "DELETE FROM contract_review_tasks WHERE review_id = %s AND user_id = %s",
                (review_id, user_id),
            )
            return str(row[0])

    async def save_report(
        self,
        review_id: str,
        user_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any] | None:
        """保存一份不可变报告版本，并返回带 report_id 的快照。"""

        report_id = str(uuid4())
        async with self._pool.connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT session_id, sha256
                FROM contract_review_tasks
                WHERE review_id = %s AND user_id = %s
                FOR UPDATE
                """,
                (review_id, user_id),
            )
            task = await cur.fetchone()
            if task is None:
                return None

            await cur.execute(
                """
                SELECT COALESCE(MAX(report_version), 0) + 1 AS next_version
                FROM contract_review_reports
                WHERE review_id = %s
                """,
                (review_id,),
            )
            version = int((await cur.fetchone())["next_version"])
            persisted = {
                **report,
                "report_id": report_id,
                "report_version": version,
                "session_id": str(task["session_id"]) if task["session_id"] else None,
            }
            canonical = json.dumps(
                persisted,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            report_hash = sha256(canonical).hexdigest()
            await cur.execute(
                """
                INSERT INTO contract_review_reports
                    (report_id, review_id, session_id, report_version,
                     workflow_status, report, input_sha256, report_sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    report_id,
                    review_id,
                    task["session_id"],
                    version,
                    persisted.get("workflow_status", "partial"),
                    Jsonb(persisted),
                    task["sha256"] or "",
                    report_hash,
                ),
            )
            return {
                "report_id": report_id,
                "report_version": version,
                "report": persisted,
                "report_sha256": report_hash,
                "session_id": str(task["session_id"]) if task["session_id"] else None,
            }

    async def get_report(self, review_id: str, user_id: str) -> dict[str, Any] | None:
        """读取用户有权访问的最新报告版本。"""

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT r.report_id, r.review_id, r.session_id, r.report_version,
                       r.report, r.report_sha256, r.assessment_date, r.created_at,
                       t.sha256 AS input_sha256
                FROM contract_review_reports r
                JOIN contract_review_tasks t ON t.review_id = r.review_id
                WHERE r.review_id = %s AND t.user_id = %s
                  AND t.deleted_at IS NULL
                  AND COALESCE(t.expires_at, t.created_at + INTERVAL '7 days') > NOW()
                ORDER BY r.report_version DESC
                LIMIT 1
                """,
                (review_id, user_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_session_reviews(
        self,
        session_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """列出当前用户会话中的合同任务及最新报告标识。"""

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT t.review_id, t.session_id, t.filename, t.status,
                       t.confirmation_status, t.created_at,
                       r.report_id, r.report_version
                FROM contract_review_tasks t
                LEFT JOIN LATERAL (
                    SELECT report_id, report_version
                    FROM contract_review_reports
                    WHERE review_id = t.review_id
                    ORDER BY report_version DESC
                    LIMIT 1
                ) r ON TRUE
                WHERE t.session_id = %s AND t.user_id = %s
                  AND t.deleted_at IS NULL
                  AND COALESCE(t.expires_at, t.created_at + INTERVAL '7 days') > NOW()
                ORDER BY t.created_at DESC
                """,
                (session_id, user_id),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def list_user_reviews(
        self,
        user_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出当前用户仍在留存期内的合同审查任务及报告标识。

        该查询不返回合同原文、事实值或证据，只返回用于最近对话导航的最小元数据。
        ``limit`` 在仓储层再次约束，避免调用方意外请求过大的历史列表。
        """

        safe_limit = max(1, min(limit, 100))
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT t.review_id, t.session_id, t.filename, t.status,
                       t.confirmation_status, t.created_at,
                       r.report_id, r.report_version
                FROM contract_review_tasks t
                LEFT JOIN LATERAL (
                    SELECT report_id, report_version
                    FROM contract_review_reports
                    WHERE review_id = t.review_id
                    ORDER BY report_version DESC
                    LIMIT 1
                ) r ON TRUE
                WHERE t.user_id = %s
                  AND t.deleted_at IS NULL
                  AND COALESCE(t.expires_at, t.created_at + INTERVAL '7 days') > NOW()
                ORDER BY t.created_at DESC
                LIMIT %s
                """,
                (user_id, safe_limit),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def get_confirmation_request(
        self,
        review_id: str,
        user_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        """查询已经处理过的 request_id，用于客户端重试幂等。"""

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT c.confirmation_id, c.request_id, t.confirmation_revision,
                       t.confirmation_status, t.confirmation_result
                FROM contract_review_fact_confirmations c
                JOIN contract_review_tasks t ON t.review_id = c.review_id
                WHERE c.review_id = %s AND t.user_id = %s AND c.request_id = %s
                ORDER BY c.created_at DESC
                LIMIT 1
                """,
                (review_id, user_id, request_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def save_confirmation_state(
        self,
        review_id: str,
        user_id: str,
        *,
        expected_revision: int,
        status: str,
        snapshot: dict[str, Any],
        events: list[dict[str, Any]],
        request_id: str | None,
    ) -> int:
        """以乐观锁原子写入快照和追加式确认事件。"""

        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE contract_review_tasks
                SET confirmation_status = %s,
                    confirmation_revision = confirmation_revision + 1,
                    confirmation_result = %s,
                    confirmed_at = CASE WHEN %s = 'completed' THEN NOW() ELSE confirmed_at END,
                    updated_at = NOW()
                WHERE review_id = %s AND user_id = %s
                  AND confirmation_revision = %s
                RETURNING confirmation_revision
                """,
                (
                    status,
                    Jsonb(snapshot),
                    status,
                    review_id,
                    user_id,
                    expected_revision,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                raise ConfirmationRevisionConflict(review_id)
            new_revision = int(row[0])

            for event in events:
                await cur.execute(
                    """
                    INSERT INTO contract_review_fact_confirmations
                        (confirmation_id, review_id, fact_id, action, user_value,
                         note, base_revision, request_id, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        review_id,
                        event["fact_id"],
                        event["action"],
                        Jsonb(event.get("user_value")) if event.get("user_value") is not None else None,
                        event.get("note"),
                        expected_revision,
                        request_id,
                        user_id,
                    ),
                )
            return new_revision

    async def get_pages(self, review_id: str) -> list[dict[str, Any]]:
        """恢复事实提取时读取已经保存的脱敏页文本。"""

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            return [dict(row) for row in await cur.fetchall()]

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

    async def list_pending_extractions(self) -> list[dict[str, Any]]:
        """列出进程重启时尚未完成的事实提取任务。"""

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                    SELECT review_id, extraction_status
                    FROM contract_review_tasks
                    WHERE status IN ('ready', 'needs_confirmation')
                      AND extraction_status = 'running'
                    ORDER BY created_at
                    """
            )
            return [dict(row) for row in await cur.fetchall()]
