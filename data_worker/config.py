"""Data Worker 运行配置。

该模块集中解析环境变量和入库常量，不打开数据库或 HTTP 连接。Watcher、CLI
和 Ingest Service 接收同一个 ``WorkerSettings``，避免各模块使用不同默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkerSettings:
    """Sentinel 监听和文档入库所需的完整配置。"""

    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "/app/data/raw")))
    qdrant_url: str = field(default_factory=lambda: os.getenv("QDRANT_URL", "http://db_qdrant:6333"))
    embed_url: str = field(
        default_factory=lambda: os.getenv("EMBED_URL", "http://embedding_service:8001/embed")
    )
    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", "127.0.0.1"))
    pg_port: int = field(default_factory=lambda: int(os.getenv("PG_PORT", "5432")))
    pg_user: str = field(default_factory=lambda: os.getenv("PG_USER", "admin"))
    pg_password: str = field(default_factory=lambda: os.getenv("PG_PASSWORD", ""))
    pg_database: str = field(default_factory=lambda: os.getenv("PG_DATABASE", "ai_assistant"))
    collection_name: str = "rag_chunks"
    vector_dim: int = 1024
    chunk_size: int = 1000
    chunk_overlap: int = 200
    embedding_batch_size: int = 2
    supported_suffixes: frozenset[str] = frozenset({".md", ".txt", ".markdown"})

    @property
    def pg_dsn(self) -> str:
        """返回 psycopg_pool 使用的 DSN。"""
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )
