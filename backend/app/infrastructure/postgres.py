"""PostgreSQL 连接池生命周期管理。

职责：根据 DSN 创建和关闭 Backend 共用的异步连接池。
不负责：执行用户、会话或检索业务 SQL；这些操作属于对应 Service。
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


def create_postgres_pool(dsn: str) -> AsyncConnectionPool:
    """创建已打开的进程级异步连接池。

    参数 ``dsn`` 由 ``Settings.pg_dsn`` 提供。连接池由 FastAPI lifespan 持有，
    所有业务服务共享同一实例，避免每个路由重复建立数据库连接。
    """
    return AsyncConnectionPool(dsn, min_size=2, max_size=8, open=True)


async def close_postgres_pool(pool: AsyncConnectionPool) -> None:
    """在应用关闭时等待连接池完成清理。"""
    await pool.close()
