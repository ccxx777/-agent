"""Backend 运行配置。

本模块只负责把环境变量转换为有类型的配置对象，不创建网络连接、模型或
数据库连接池。外部资源的创建统一交给 ``app.infrastructure``，从而保证
导入配置模块不会产生副作用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_flag(name: str, default: bool = False) -> bool:
    """解析显式布尔环境变量，避免字符串真值造成治理开关误开。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    rag_collection: str = field(default_factory=lambda: os.getenv("RAG_COLLECTION", "rag_chunks"))
    # 合同 Workflow 使用独立的法律资料库；不能误用通用 RAG 或 watsonx 评测库。
    legal_a_collection: str = field(default_factory=lambda: os.getenv("LEGAL_A_COLLECTION", ""))
    legal_b_collection: str = field(default_factory=lambda: os.getenv("LEGAL_B_COLLECTION", ""))
    legal_a_allow_pending_governance: bool = field(
        default_factory=lambda: _env_flag("LEGAL_A_ALLOW_PENDING_GOVERNANCE")
    )
    legal_b_allow_pending_governance: bool = field(
        default_factory=lambda: _env_flag("LEGAL_B_ALLOW_PENDING_GOVERNANCE")
    )

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

    # ── 合同文件接入 ──
    contract_storage_dir: str = field(
        default_factory=lambda: os.getenv("CONTRACT_STORAGE_DIR", "/app/private-data/contracts")
    )
    contract_max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("CONTRACT_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    )
    contract_max_pages: int = field(
        default_factory=lambda: int(os.getenv("CONTRACT_MAX_PAGES", "50"))
    )
    contract_doc_command: str = field(
        default_factory=lambda: os.getenv("CONTRACT_DOC_COMMAND", "antiword")
    )
    contract_document_timeout: float = field(
        default_factory=lambda: float(os.getenv("CONTRACT_DOCUMENT_TIMEOUT", "30"))
    )
    contract_ocr_enabled: bool = field(
        default_factory=lambda: os.getenv("CONTRACT_OCR_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    contract_ocr_base_url: str = field(
        default_factory=lambda: os.getenv("CONTRACT_OCR_BASE_URL", "https://api.siliconflow.cn/v1")
    )
    contract_ocr_api_key: str = field(
        default_factory=lambda: os.getenv(
            "CONTRACT_OCR_API_KEY", os.getenv("SILICONFLOW_API_KEY", "")
        )
    )
    contract_ocr_model: str = field(
        default_factory=lambda: os.getenv("CONTRACT_OCR_MODEL", "deepseek-ai/DeepSeek-OCR")
    )
    contract_extraction_enabled: bool = field(
        default_factory=lambda: os.getenv("CONTRACT_EXTRACTION_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    contract_extraction_batch_clauses: int = field(
        default_factory=lambda: int(os.getenv("CONTRACT_EXTRACTION_BATCH_CLAUSES", "6"))
    )
    contract_extraction_max_chars: int = field(
        default_factory=lambda: int(os.getenv("CONTRACT_EXTRACTION_MAX_CHARS", "12000"))
    )
    contract_extraction_single_pass_max_chars: int = field(
        default_factory=lambda: int(
            os.getenv("CONTRACT_EXTRACTION_SINGLE_PASS_MAX_CHARS", "12000")
        )
    )

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
