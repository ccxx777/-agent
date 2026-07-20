"""FastAPI 应用装配入口。

``main`` 是唯一允许创建外部基础设施并连接各层的 Composition Root：
配置 → Infrastructure → Services → Agent → API。业务逻辑不写在这里，关闭时
只清理由本模块创建的进程级资源。
"""

# 环境文件必须在导入读取 Settings 的模块之前加载。
# ruff: noqa: E402

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

# 必须最先执行，以项目 .env 覆盖宿主进程中同名变量。
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", "/app/.env"), override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.agent.graph import get_compiled_graph
from app.api.auth import create_auth_router
from app.api.chat import create_chat_router
from app.api.eval import create_eval_router
from app.api.sessions import create_sessions_router
from app.config import settings
from app.infrastructure.embedding_client import EmbeddingClient
from app.infrastructure.model_provider import ModelProvider
from app.infrastructure.postgres import close_postgres_pool, create_postgres_pool
from app.infrastructure.qdrant import QdrantGateway
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.retrieval_service import RetrievalService
from app.services.session_service import SessionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """创建共享资源、挂载路由，并在进程关闭时释放连接池。"""
    logger.info("Starting AI Assistant backend...")

    pg_pool = create_postgres_pool(settings.pg_dsn)
    app.state.pg_pool = pg_pool

    auth_service = AuthService(pg_pool)
    retrieval_service = RetrievalService(
        embedding_client=EmbeddingClient(settings.embedding_endpoint),
        qdrant=QdrantGateway(settings.qdrant_url),
        reranker_model=settings.reranker_model,
        reranker_api_url=settings.reranker_api_url,
        reranker_api_key=settings.reranker_api_key,
        collection_name=settings.rag_collection,
    )
    model_provider = ModelProvider(
        model=settings.main_model,
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )
    graph = get_compiled_graph(
        pg_pool,
        retrieval_service=retrieval_service,
        model_provider=model_provider,
    )
    chat_service = ChatService(graph)
    session_service = SessionService(graph)

    app.state.auth_service = auth_service
    app.state.retrieval_service = retrieval_service
    app.state.chat_service = chat_service
    app.state.session_service = session_service

    app.include_router(create_auth_router(auth_service))
    app.include_router(create_chat_router(chat_service))
    app.include_router(create_sessions_router(session_service))
    app.include_router(create_eval_router(chat_service))

    logger.info("Backend ready")
    yield

    await close_postgres_pool(pg_pool)
    logger.info("Shutting down...")


def create_app() -> FastAPI:
    """创建不触发外部连接的 FastAPI 应用对象。"""
    app = FastAPI(title="AI Assistant", version="0.1.0", lifespan=lifespan)

    # CORS — 允许 Horizon UI 跨域请求
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
