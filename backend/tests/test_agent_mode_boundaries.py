"""Agent 模式与资料源 allowlist 的回归测试。"""

from __future__ import annotations

import json
import unittest

from app.agent.graph import ModeAwareToolNode, _route_after_chat
from langchain_core.messages import AIMessage
from langchain_core.tools import tool


class AgentModeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_legal_mode_blocks_general_retrieval_tool(self):
        @tool
        async def search_knowledge_base(query: str) -> str:
            """测试用通用检索工具。"""
            return query

        node = ModeAwareToolNode([search_knowledge_base])
        result = await node(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "search_knowledge_base",
                                "args": {"query": "q"},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
                "conversation_mode": "legal",
            }
        )

        payload = json.loads(result["messages"][0].content)
        self.assertEqual(payload["error"], "tool_not_allowed_for_mode")

    def test_contract_review_without_tool_call_goes_to_final_answer(self):
        state = {
            "messages": [AIMessage(content="")],
            "conversation_mode": "contract_review",
        }
        self.assertEqual(_route_after_chat(state), "generate_answer")

    async def test_blocked_batch_returns_one_tool_result_per_call(self):
        @tool
        async def search_knowledge_base(query: str) -> str:
            """测试用通用检索工具。"""
            return query

        node = ModeAwareToolNode([search_knowledge_base])
        result = await node(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "search_knowledge_base",
                                "args": {"query": "q1"},
                                "id": "call-1",
                                "type": "tool_call",
                            },
                            {
                                "name": "search_legal_knowledge_base",
                                "args": {"query": "q2"},
                                "id": "call-2",
                                "type": "tool_call",
                            },
                        ],
                    )
                ],
                "conversation_mode": "legal",
            }
        )

        self.assertEqual(
            [message.tool_call_id for message in result["messages"]],
            ["call-1", "call-2"],
        )
        self.assertEqual(
            json.loads(result["messages"][0].content)["error"],
            "tool_not_allowed_for_mode",
        )
        self.assertEqual(
            json.loads(result["messages"][1].content)["error"],
            "mixed_tool_batch_rejected",
        )


if __name__ == "__main__":
    unittest.main()
