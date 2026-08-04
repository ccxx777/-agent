#!/usr/bin/env python3
"""检查合同报告相关数据库迁移的最终 schema。

该脚本只读取 PostgreSQL 的 information_schema，不执行迁移、不修改数据，也不
输出连接密码。它验证 005/006/007 迁移产生的表、字段和关键索引是否存在。

注意：数据库没有统一的迁移版本表时，本脚本只能证明“目标 schema 已满足”，
不能证明某个 SQL 文件曾经由某个命令执行过。生产验收应同时保留 SQL 命令和
本脚本的 JSON 结果。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MigrationCheckError(RuntimeError):
    """数据库 schema 检查失败。"""


REQUIRED_COLUMNS: dict[str, set[str]] = {
    "contract_review_tasks": {
        "review_id",
        "session_id",
        "retention_policy",
        "expires_at",
        "deleted_at",
    },
    "contract_review_reports": {
        "report_id",
        "review_id",
        "session_id",
        "report_version",
        "workflow_status",
        "report",
        "assessment_date",
        "input_sha256",
        "report_sha256",
        "legal_corpus_version",
        "rule_version",
        "prompt_version",
        "model_version",
        "parser_version",
    },
    "sessions": {
        "session_id",
        "conversation_scope_version",
        "has_contract_context",
    },
}

REQUIRED_INDEXES = {
    "idx_contract_review_tasks_session_created",
    "idx_contract_review_tasks_expiry",
    "idx_contract_review_reports_review_created",
    "idx_contract_review_reports_session_created",
}


def evaluate_schema(
    *,
    tables: set[str],
    columns: dict[str, set[str]],
    indexes: set[str],
) -> dict[str, Any]:
    """根据已读取的 schema 快照生成不连接数据库的验收结果。"""

    table_checks: dict[str, bool] = {}
    missing_columns: dict[str, list[str]] = {}
    for table, required in REQUIRED_COLUMNS.items():
        table_checks[table] = table in tables
        missing = sorted(required - columns.get(table, set()))
        if missing:
            missing_columns[table] = missing

    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    checks = {
        "migration_005_session_and_reports": table_checks["contract_review_reports"]
        and not missing_columns.get("contract_review_tasks", [])
        and not missing_columns.get("contract_review_reports", []),
        "migration_006_retention": not missing_columns.get("contract_review_tasks", [])
        and "contract_review_tasks" in tables,
        "migration_007_conversation_scope": not missing_columns.get("sessions", [])
        and "sessions" in tables,
        "required_indexes": not missing_indexes,
    }
    return {
        "format_version": 1,
        "status": "passed" if all(checks.values()) else "failed",
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "tables": sorted(tables),
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
    }


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查合同 005/006/007 数据库迁移结果")
    parser.add_argument("--host", default=_env("PG_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(_env("PG_PORT", "5432")))
    parser.add_argument("--user", default=_env("PG_USER", "admin"))
    parser.add_argument("--password", default=_env("PG_PASSWORD"), help=argparse.SUPPRESS)
    parser.add_argument("--password-env", default="PG_PASSWORD", help=argparse.SUPPRESS)
    parser.add_argument("--database", default=_env("PG_DATABASE", "ai_assistant"))
    parser.add_argument("--output", type=Path)
    return parser


def _read_snapshot(args: argparse.Namespace) -> tuple[set[str], dict[str, set[str]], set[str]]:
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - backend image supplies psycopg
        raise MigrationCheckError("缺少 psycopg，请使用 backend/requirements.txt 环境运行") from error

    password = args.password or os.getenv(args.password_env, "")
    try:
        with psycopg.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=password,
            dbname=args.database,
            connect_timeout=10,
        ) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            tables = {str(row[0]) for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                """
            )
            columns: dict[str, set[str]] = {}
            for table, column in cursor.fetchall():
                columns.setdefault(str(table), set()).add(str(column))

            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                """
            )
            indexes = {str(row[0]) for row in cursor.fetchall()}
    except Exception as error:
        raise MigrationCheckError(f"无法读取 PostgreSQL schema：{type(error).__name__}") from error
    return tables, columns, indexes


def run_check(args: argparse.Namespace) -> dict[str, Any]:
    tables, columns, indexes = _read_snapshot(args)
    return evaluate_schema(tables=tables, columns=columns, indexes=indexes)


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = run_check(args)
    except MigrationCheckError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
