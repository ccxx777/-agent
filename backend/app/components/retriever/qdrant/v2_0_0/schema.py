"""retriever.qdrant Schema — 输入输出数据契约。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrieveInput(BaseModel):
    """检索输入"""
    query: str = Field(..., min_length=1, description="查询文本")
    top_k: int = Field(10, ge=1, le=100, description="返回条数")
    qdrant_url: str = Field("http://localhost:6333", description="Qdrant 服务地址")
    embedding_url: str = Field("http://embedding_service:8001/embed", description="Embedding 服务地址")
    search_types: list[str] = Field(default=["dense", "sparse", "fulltext"], description="召回类型")


class DocItem(BaseModel):
    """单条检索结果"""
    doc_id: str = ""
    chunk_id: str = ""
    chunk_text: str = ""
    title: str = ""
    source: str = ""
    score: float = 0.0
    scores_per_path: dict = Field(default_factory=dict)


class RetrieveOutput(BaseModel):
    """检索输出"""
    query: str = ""
    docs: list[DocItem] = Field(default_factory=list)
    elapsed_ms: float = 0.0
    total: int = 0
