"""Backend 运行配置。

本模块只负责把环境变量转换为有类型的配置对象，不创建网络连接、模型或
数据库连接池。外部资源的创建统一交给 ``app.infrastructure``，从而保证
导入配置模块不会产生副作用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """应用进程使用的不可变配置来源。

    字段名称保持与 Docker Compose 的环境变量一致。这里保留默认值是为了
    方便本地导入和静态测试；生产环境中的密码和 API Key 必须由环境变量提供。
    """

    # ── 数据库连接（容器名，非 localhost） ──
    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", "db_pg"))
    pg_port: int = field(default_factory=lambda: int(os.getenv("PG_PORT", "5432")))
    pg_user: str = field(default_factory=lambda: os.getenv("PG_USER", "admin"))
    pg_password: str = field(default_factory=lambda: os.getenv("PG_PASSWORD", ""))
    pg_database: str = field(default_factory=lambda: os.getenv("PG_DATABASE", "ai_assistant"))

    qdrant_url: str = field(default_factory=lambda: os.getenv("QDRANT_URL", "http://db_qdrant:6333"))

    # ── Embedding 服务 ──
    embedding_url: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_URL", "http://embedding_service:8001")
    )

    reranker_model: str = field(
        default_factory=lambda: os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    )
    reranker_api_url: str = field(
        default_factory=lambda: os.getenv("RERANKER_API_URL", "https://api.siliconflow.cn/v1/rerank")
    )
    reranker_api_key: str = field(
        default_factory=lambda: os.getenv("RERANKER_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN", ""))
    )

    # ── LLM ──
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_AUTH_TOKEN", ""))
    main_model: str = field(default_factory=lambda: os.getenv("MAIN_MODEL", "deepseek-v4-flash"))

    # ── Server ──
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def pg_dsn(self) -> str:
        """返回 psycopg 使用的 PostgreSQL DSN。"""
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def embedding_endpoint(self) -> str:
        """返回 Embedding HTTP 端点，并兼容历史 ``EMBED_URL`` 配置。"""
        return os.getenv("EMBED_URL", f"{self.embedding_url.rstrip('/')}/embed")


settings = Settings()
