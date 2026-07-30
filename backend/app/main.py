"""FastAPI 应用装配入口。

``main`` 是唯一允许创建外部基础设施并连接各层的 Composition Root：
配置 → Infrastructure → Services → Agent → API。业务逻辑不写在这里，关闭时
只清理由本模块创建的进程级资源。
"""

# 环境文件必须在导入读取 Settings 的模块之前加载。

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

# 必须最先执行，以项目 .env 覆盖宿主进程中同名变量。
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.getenv("DOTENV_PATH", "/app/.env"), override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.contract_review_workflow import build_contract_review_workflow
from app.agent.graph import get_compiled_graph
from app.api.auth import create_auth_router
from app.api.chat import create_chat_router
from app.api.contract_reviews import create_contract_review_router
from app.api.eval import create_eval_router
from app.api.sessions import create_sessions_router
from app.config import settings
from app.infrastructure.contract_document import ContractDocumentParser
from app.infrastructure.contract_ocr import ContractOCRClient
from app.infrastructure.contract_review_repository import ContractReviewRepository
from app.infrastructure.contract_storage import PrivateContractStorage
from app.infrastructure.embedding_client import EmbeddingClient
from app.infrastructure.model_provider import ModelProvider
from app.infrastructure.postgres import close_postgres_pool, create_postgres_pool
from app.infrastructure.qdrant import QdrantGateway
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.contract_confirmation_service import ContractFactConfirmationService
from app.services.contract_extraction_service import ContractExtractionService
from app.services.contract_review_service import ContractReviewService
from app.services.contract_review_workflow_service import ContractReviewWorkflowService
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
    contract_repository = ContractReviewRepository(pg_pool)
    model_provider = ModelProvider(
        model=settings.main_model,
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )
    extraction_service = (
        ContractExtractionService(
            contract_repository,
            chat_model=model_provider.create_chat_model(),
            model_name=settings.main_model,
            batch_clauses=settings.contract_extraction_batch_clauses,
            max_model_chars=settings.contract_extraction_max_chars,
        )
        if settings.contract_extraction_enabled
        else None
    )
    contract_review_service = ContractReviewService(
        repository=contract_repository,
        storage=PrivateContractStorage(settings.contract_storage_dir),
        parser=ContractDocumentParser(
            doc_command=settings.contract_doc_command,
            doc_timeout=settings.contract_document_timeout,
        ),
        ocr_client=ContractOCRClient(
            enabled=settings.contract_ocr_enabled,
            base_url=settings.contract_ocr_base_url,
            api_key=settings.contract_ocr_api_key,
            model=settings.contract_ocr_model,
        ),
        max_upload_bytes=settings.contract_max_upload_bytes,
        max_pages=settings.contract_max_pages,
        extraction_service=extraction_service,
    )
    contract_confirmation_service = ContractFactConfirmationService(contract_repository)
    embedding_client = EmbeddingClient(settings.embedding_endpoint)
    qdrant_gateway = QdrantGateway(settings.qdrant_url)
    retrieval_service = RetrievalService(
        embedding_client=embedding_client,
        qdrant=qdrant_gateway,
        reranker_model=settings.reranker_model,
        reranker_api_url=settings.reranker_api_url,
        reranker_api_key=settings.reranker_api_key,
        collection_name=settings.rag_collection,
    )
    legal_a_retrieval_service = RetrievalService(
        embedding_client=embedding_client,
        qdrant=qdrant_gateway,
        reranker_model=settings.reranker_model,
        reranker_api_url=settings.reranker_api_url,
        reranker_api_key=settings.reranker_api_key,
        collection_name=settings.legal_a_collection,
    )
    legal_b_retrieval_service = RetrievalService(
        embedding_client=embedding_client,
        qdrant=qdrant_gateway,
        reranker_model=settings.reranker_model,
        reranker_api_url=settings.reranker_api_url,
        reranker_api_key=settings.reranker_api_key,
        collection_name=settings.legal_b_collection,
    )
    graph = get_compiled_graph(
        pg_pool,
        retrieval_service=retrieval_service,
        model_provider=model_provider,
    )
    chat_service = ChatService(graph)
    session_service = SessionService(graph)
    contract_review_workflow = build_contract_review_workflow(
        repository=contract_repository,
        confirmation_service=contract_confirmation_service,
        legal_retrieval_service=legal_a_retrieval_service,
        case_retrieval_service=legal_b_retrieval_service,
    )
    contract_review_workflow_service = ContractReviewWorkflowService(contract_review_workflow)

    app.state.auth_service = auth_service
    app.state.retrieval_service = retrieval_service
    app.state.chat_service = chat_service
    app.state.session_service = session_service
    app.state.contract_review_service = contract_review_service
    app.state.contract_confirmation_service = contract_confirmation_service
    app.state.contract_review_workflow_service = contract_review_workflow_service
    contract_recovery_task = asyncio.create_task(contract_review_service.resume_pending())
    app.state.contract_recovery_task = contract_recovery_task

    app.include_router(create_auth_router(auth_service))
    app.include_router(create_chat_router(chat_service))
    app.include_router(create_sessions_router(session_service))
    app.include_router(
        create_contract_review_router(
            contract_review_service,
            contract_confirmation_service,
            contract_review_workflow_service,
        )
    )
    app.include_router(create_eval_router(chat_service))

    logger.info("Backend ready")
    yield

    await close_postgres_pool(pg_pool)
    if not contract_recovery_task.done():
        contract_recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await contract_recovery_task
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
