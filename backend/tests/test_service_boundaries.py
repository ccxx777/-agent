"""结构重构后的 Service 边界测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.components.retriever.qdrant.v2_0_0.main import _sparse_search_scored
from app.infrastructure.contract_review_repository import SessionOwnershipError
from app.infrastructure.embedding_client import EmbeddingClient
from app.services.chat_service import (
    ChatReportNotFound,
    ChatReportSessionMismatch,
    ChatService,
)
from app.services.retrieval_service import RetrievalService
from app.services.session_service import SessionService
from langchain_core.messages import AIMessage, HumanMessage


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


class _NativeSparseClient:
    def __init__(self) -> None:
        self.search_calls: list[dict] = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [SimpleNamespace(id="point", score=0.9, payload={})]

    def scroll(self, **kwargs):  # pragma: no cover - 调用即代表原生路径失效
        raise AssertionError("v2 native sparse path must not scan payloads")


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
            collection_name="knowledge_v2",
        )

        with patch(
            "app.services.retrieval_service.get_final_funnel_top3",
            new=AsyncMock(return_value=ranked_hits),
        ) as funnel:
            result = await service.retrieve("question")

        self.assertEqual([doc.point_id for doc in result.documents], ["second", "first"])
        self.assertEqual([doc.rank for doc in result.documents], [1, 2])
        funnel.assert_awaited_once()
        self.assertEqual(funnel.await_args.kwargs["collection_name"], "knowledge_v2")

    async def test_native_sparse_path_does_not_scroll_payloads(self):
        client = _NativeSparseClient()

        results = _sparse_search_scored(
            client,
            {2026: 0.12, 562: 0.18},
            10,
            "knowledge_v2",
            {"bge_m3_sparse"},
        )

        self.assertEqual(results[0][0], 0.9)
        self.assertEqual(len(client.search_calls), 1)
        query_vector = client.search_calls[0]["query_vector"]
        self.assertEqual(query_vector.name, "bge_m3_sparse")

    async def test_chat_service_keeps_thread_and_user_ids(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]})
        )
        service = ChatService(graph)

        answer = await service.ask(query="q", session_id="session", user_id="user")

        self.assertEqual(answer, "answer")
        graph.ainvoke.assert_awaited_once_with(
            {
                "messages": [unittest.mock.ANY],
                "conversation_mode": "general",
                "active_review_id": "",
                "contract_context": "",
                "report_context": "",
            },
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
            "messages": [{"role": "assistant", "content": "answer"}],
            "summary": "memory",
        })

    async def test_session_service_rejects_history_from_another_user(self):
        graph = SimpleNamespace(aget_state=AsyncMock())
        repository = SimpleNamespace(
            get_session_owner=AsyncMock(return_value="owner-1"),
        )

        with self.assertRaises(SessionOwnershipError):
            await SessionService(graph, repository).get_history("session", "owner-2")

        graph.aget_state.assert_not_awaited()

    async def test_chat_service_creates_or_checks_session_before_graph_call(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
        )
        repository = SimpleNamespace(ensure_session=AsyncMock())
        service = ChatService(graph, repository)

        await service.ask(
            query="q",
            session_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
        )

        repository.ensure_session.assert_awaited_once_with(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )

    async def test_chat_service_marks_legacy_contract_checkpoint_after_ownership_check(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={
                        "conversation_mode": "contract_review",
                        "active_review_id": "old-review",
                        "contract_context": "旧合同正文",
                    }
                )
            ),
        )
        repository = SimpleNamespace(ensure_session=AsyncMock())
        service = ChatService(graph, repository)
        session_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"

        await service.ask(query="现在的问题", session_id=session_id, user_id=user_id)

        repository.ensure_session.assert_awaited_once_with(session_id, user_id)
        graph.aget_state.assert_awaited_once_with({"configurable": {"thread_id": session_id}})
        self.assertTrue(graph.ainvoke.await_args.args[0]["legacy_unscoped_messages"])

    async def test_chat_service_marks_cleared_legacy_contract_history_by_message_scope(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={
                        "conversation_mode": "general",
                        "active_review_id": "",
                        "contract_context": "",
                        "report_context": "",
                        "messages": [HumanMessage(content="旧合同工资为 5000 元")],
                    }
                )
            ),
        )
        repository = SimpleNamespace(ensure_session=AsyncMock())
        service = ChatService(graph, repository)
        session_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"

        await service.ask(query="升级后的普通问题", session_id=session_id, user_id=user_id)

        self.assertTrue(graph.ainvoke.await_args.args[0]["legacy_unscoped_messages"])

    async def test_chat_service_uses_persisted_scope_decision_without_scanning(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
            aget_state=AsyncMock(),
        )
        repository = SimpleNamespace(
            ensure_session=AsyncMock(),
            get_conversation_scope_state=AsyncMock(
                return_value={"conversation_scope_version": 2, "has_contract_context": True}
            ),
            mark_conversation_scope_state=AsyncMock(),
        )
        service = ChatService(graph, repository)
        session_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"

        await service.ask(query="旧合同问题", session_id=session_id, user_id=user_id)

        graph.aget_state.assert_not_awaited()
        repository.mark_conversation_scope_state.assert_not_awaited()
        self.assertTrue(graph.ainvoke.await_args.args[0]["legacy_unscoped_messages"])

    async def test_chat_service_keeps_legacy_general_memory_when_no_contract_history(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
            aget_state=AsyncMock(),
        )
        repository = SimpleNamespace(
            ensure_session=AsyncMock(),
            get_conversation_scope_state=AsyncMock(
                return_value={"conversation_scope_version": None, "has_contract_context": False}
            ),
            mark_conversation_scope_state=AsyncMock(),
        )
        service = ChatService(graph, repository)
        session_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"

        await service.ask(query="旧普通问题", session_id=session_id, user_id=user_id)

        graph.aget_state.assert_not_awaited()
        repository.mark_conversation_scope_state.assert_awaited_once_with(session_id, user_id, 1)
        self.assertNotIn("legacy_unscoped_messages", graph.ainvoke.await_args.args[0])

    async def test_chat_service_isolates_deleted_contract_history_after_conservative_migration(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={
                        "conversation_mode": "general",
                        "active_review_id": "",
                        "contract_context": "",
                        "messages": [HumanMessage(content="已删除合同中的工资")],
                    }
                )
            ),
        )
        repository = SimpleNamespace(
            ensure_session=AsyncMock(),
            get_conversation_scope_state=AsyncMock(
                return_value={"conversation_scope_version": None, "has_contract_context": True}
            ),
            mark_conversation_scope_state=AsyncMock(),
        )
        service = ChatService(graph, repository)
        session_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"

        await service.ask(query="删除合同后的普通问题", session_id=session_id, user_id=user_id)

        self.assertTrue(graph.ainvoke.await_args.args[0]["legacy_unscoped_messages"])
        repository.mark_conversation_scope_state.assert_awaited_once_with(session_id, user_id, 2)

    async def test_chat_service_binds_report_context_to_same_session(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
        )
        repository = SimpleNamespace(
            ensure_session=AsyncMock(),
            get_task=AsyncMock(
                return_value={
                    "review_id": "review-1",
                    "session_id": "00000000-0000-0000-0000-000000000001",
                    "filename": "劳动合同.doc",
                    "status": "needs_confirmation",
                    "extraction_status": "needs_confirmation",
                    "confirmation_status": "completed",
                    "pages": [{"page_no": 1, "text": "月工资 8000 元。"}],
                    "extraction_result": {
                        "facts": [{"field_key": "salary", "value": "8000 元"}],
                    },
                    "confirmation_result": {
                        "facts": [{"field_key": "salary", "effective_value": "8000 元"}],
                    },
                }
            ),
            get_report=AsyncMock(
                return_value={
                    "session_id": "00000000-0000-0000-0000-000000000001",
                    "report_version": 1,
                    "report": {
                        "scope": "labor_contract_national",
                        "findings": [],
                        "pending_questions": [],
                        "legal_sources": [],
                        "disclaimer": "仅供参考",
                    },
                }
            ),
        )
        service = ChatService(graph, repository)

        await service.invoke(
            query="这份报告的结论是什么？",
            session_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            mode="contract_review",
            review_id="review-1",
        )

        input_state = graph.ainvoke.await_args.args[0]
        self.assertEqual(input_state["conversation_mode"], "contract_review")
        self.assertEqual(input_state["active_review_id"], "review-1")
        self.assertEqual(input_state["summary"], "")
        self.assertIn("月工资 8000 元", input_state["contract_context"])
        self.assertIn('"effective_value": "8000 元"', input_state["contract_context"])
        self.assertIn("labor_contract_national", input_state["contract_context"])
        self.assertEqual(
            graph.ainvoke.await_args.args[1]["configurable"]["thread_id"],
            "00000000-0000-0000-0000-000000000001",
        )
        repository.get_task.assert_awaited_once_with(
            "review-1", "00000000-0000-0000-0000-000000000002"
        )
        repository.get_report.assert_awaited_once_with(
            "review-1", "00000000-0000-0000-0000-000000000002"
        )

    async def test_chat_service_reuses_session_before_and_after_contract_upload(self):
        graph = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="answer")]}),
        )
        repository = SimpleNamespace(
            ensure_session=AsyncMock(),
            get_task=AsyncMock(
                return_value={
                    "review_id": "review-1",
                    "session_id": "00000000-0000-0000-0000-000000000001",
                    "pages": [{"page_no": 1, "text": "工资为 8000 元。"}],
                    "extraction_result": {"facts": []},
                }
            ),
        )
        service = ChatService(graph, repository)
        session_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"

        await service.ask(
            query="劳动合同需要约定哪些内容？",
            session_id=session_id,
            user_id=user_id,
        )
        await service.ask(
            query="这份合同写的工资是多少？",
            session_id=session_id,
            user_id=user_id,
            review_id="review-1",
        )
        await service.ask(
            query="再给我讲一个通用的统计学概念。",
            session_id=session_id,
            user_id=user_id,
        )

        self.assertEqual(
            [call.args[1]["configurable"]["thread_id"] for call in graph.ainvoke.await_args_list],
            [session_id, session_id, session_id],
        )
        self.assertEqual(
            [call.args[0]["conversation_mode"] for call in graph.ainvoke.await_args_list],
            ["general", "contract_review", "general"],
        )
        self.assertEqual(graph.ainvoke.await_args_list[0].args[0]["active_review_id"], "")
        self.assertEqual(graph.ainvoke.await_args_list[1].args[0]["active_review_id"], "review-1")
        self.assertEqual(graph.ainvoke.await_args_list[2].args[0]["active_review_id"], "")
        contract_message = graph.ainvoke.await_args_list[1].args[0]["messages"][0]
        self.assertEqual(
            contract_message.additional_kwargs["conversation_scope"],
            "contract:review-1",
        )
        self.assertEqual(repository.ensure_session.await_count, 3)

    async def test_chat_service_deletes_report_scoped_thread(self):
        checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
        graph = SimpleNamespace(checkpointer=checkpointer)

        await ChatService(graph).delete_report_thread("review-1")

        checkpointer.adelete_thread.assert_awaited_once_with("contract-review:review-1")

    async def test_session_service_reads_report_scoped_thread(self):
        state = SimpleNamespace(
            values={"messages": [AIMessage(content="report answer")], "summary": "report memory"}
        )
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=state))
        repository = SimpleNamespace(
            get_report=AsyncMock(return_value={"report_id": "report-1"}),
        )

        result = await SessionService(graph, repository).get_report_history("review-1", "owner-1")

        self.assertEqual(result, {
            "messages": [{"role": "assistant", "content": "report answer"}],
            "summary": "report memory",
        })
        graph.aget_state.assert_awaited_once_with(
            {"configurable": {"thread_id": "contract-review:review-1"}}
        )
        repository.get_report.assert_awaited_once_with("review-1", "owner-1")

    async def test_chat_service_rejects_non_uuid_session_for_authenticated_user(self):
        graph = SimpleNamespace(ainvoke=AsyncMock())
        repository = SimpleNamespace(ensure_session=AsyncMock())
        service = ChatService(graph, repository)

        with self.assertRaises(ValueError):
            await service.ask(
                query="q",
                session_id="shared",
                user_id="00000000-0000-0000-0000-000000000002",
            )
        graph.ainvoke.assert_not_awaited()
        repository.ensure_session.assert_not_awaited()

    async def test_chat_service_requires_report_and_matching_session(self):
        graph = SimpleNamespace(ainvoke=AsyncMock())
        repository = SimpleNamespace(
            ensure_session=AsyncMock(),
            get_task=AsyncMock(return_value=None),
            get_report=AsyncMock(return_value=None),
        )
        service = ChatService(graph, repository)

        with self.assertRaises(ChatReportNotFound):
            await service.invoke(
                query="q",
                session_id="00000000-0000-0000-0000-000000000001",
                user_id="00000000-0000-0000-0000-000000000002",
                mode="contract_review",
            )

        repository.get_task.return_value = {
            "session_id": "00000000-0000-0000-0000-000000000003",
        }
        with self.assertRaises(ChatReportSessionMismatch):
            await service.invoke(
                query="q",
                session_id="00000000-0000-0000-0000-000000000001",
                user_id="00000000-0000-0000-0000-000000000002",
                mode="contract_review",
                review_id="review-1",
            )


if __name__ == "__main__":
    unittest.main()
