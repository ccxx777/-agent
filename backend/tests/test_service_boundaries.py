"""结构重构后的 Service 边界测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.infrastructure.embedding_client import EmbeddingClient
from app.services.chat_service import ChatService
from app.services.retrieval_service import RetrievalService
from app.services.session_service import SessionService


class _FakeEmbeddingClient:
    async def embed_query(self, text: str):
        return [0.1, 0.2], {7: 0.8}


class _FakeEmbeddingResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {
            "dense": [[0.1, 0.2]],
            "sparse": [{"2026": 0.12, "562": 0.18}],
        }


class _FakeAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, endpoint: str, *, json: dict):
        return _FakeEmbeddingResponse()


class ServiceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_client_normalizes_json_sparse_keys_to_int(self):
        with patch("app.infrastructure.embedding_client.httpx.AsyncClient", _FakeAsyncClient):
            dense, sparse = await EmbeddingClient("http://embedding/embed").embed_query("问题")

        self.assertEqual(dense, [0.1, 0.2])
        self.assertEqual(sparse, {2026: 0.12, 562: 0.18})
        self.assertTrue(all(isinstance(token_id, int) for token_id in sparse))

    async def test_retrieval_service_preserves_funnel_order(self):
        ranked_hits = [
            {"id": "second", "payload": {"chunk_id": "c2", "chunk_text": "B"}},
            {"id": "first", "payload": {"chunk_id": "c1", "chunk_text": "A"}},
        ]
        service = RetrievalService(
            embedding_client=_FakeEmbeddingClient(),
            qdrant=SimpleNamespace(url="http://qdrant"),
            reranker_model="reranker",
            reranker_api_url="http://reranker",
            reranker_api_key="key",
        )

        with patch(
            "app.services.retrieval_service.get_final_funnel_top3",
            new=AsyncMock(return_value=ranked_hits),
        ) as funnel:
            result = await service.retrieve("question")

        self.assertEqual([doc.point_id for doc in result.documents], ["second", "first"])
        self.assertEqual([doc.rank for doc in result.documents], [1, 2])
        funnel.assert_awaited_once()

    async def test_chat_service_keeps_thread_and_user_ids(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]})
        )
        service = ChatService(graph)

        answer = await service.ask(query="q", session_id="session", user_id="user")

        self.assertEqual(answer, "answer")
        graph.ainvoke.assert_awaited_once_with(
            {"messages": [unittest.mock.ANY]},
            {"configurable": {"thread_id": "session", "user_id": "user"}},
        )

    async def test_session_service_filters_non_conversation_messages(self):
        state = SimpleNamespace(
            values={
                "messages": [AIMessage(content="answer"), SimpleNamespace(content="ignored")],
                "summary": "memory",
            }
        )
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=state))

        result = await SessionService(graph).get_history("session")

        self.assertEqual(result, {
            "messages": [{"role": "ai", "content": "answer"}],
            "summary": "memory",
        })


if __name__ == "__main__":
    unittest.main()
