"""Agent 模式与资料源 allowlist 的回归测试。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, Mock

from app.agent.graph import ModeAwareToolNode, _route_after_chat
from app.agent.nodes import AgentNodes
from app.agent.prompts import ANSWER_PROMPT
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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

    async def test_contract_review_allows_legal_retrieval_tool(self):
        @tool
        async def search_legal_knowledge_base(query: str) -> str:
            """返回法律检索结果的测试工具。"""
            return f"legal:{query}"

        node = ModeAwareToolNode([search_legal_knowledge_base])
        delegated = {"messages": [ToolMessage(content="legal:劳动合同工资", tool_call_id="legal-call-1")]}
        node._tool_node.ainvoke = AsyncMock(return_value=delegated)
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_legal_knowledge_base",
                            "args": {"query": "劳动合同工资"},
                            "id": "legal-call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "conversation_mode": "contract_review",
        }
        result = await node(state)

        node._tool_node.ainvoke.assert_awaited_once_with(state)
        self.assertEqual(result["messages"][0].content, delegated["messages"][0].content)
        self.assertEqual(
            result["messages"][0].additional_kwargs["conversation_scope"],
            "contract_review",
        )

    def test_contract_review_without_tool_call_goes_to_final_answer(self):
        state = {
            "messages": [AIMessage(content="")],
            "conversation_mode": "contract_review",
        }
        self.assertEqual(_route_after_chat(state), "generate_answer")

    async def test_general_mode_filters_contract_messages_and_condenses_safe_history(self):
        llm_with_tools = Mock()
        llm_with_tools.invoke.return_value = AIMessage(content="")
        llm = Mock()
        llm.ainvoke = AsyncMock(return_value=Mock(content="safe summary"))
        nodes = AgentNodes(llm=llm, llm_with_tools=llm_with_tools)
        contract_human = HumanMessage(
            content="合同工资为 8000 元",
            id="contract-human",
            additional_kwargs={"conversation_scope": "contract:review-1"},
        )
        contract_ai = AIMessage(
            content="合同工资是 8000 元",
            id="contract-ai",
            additional_kwargs={"conversation_scope": "contract:review-1"},
        )
        state = {
            "messages": [
                *[
                    HumanMessage(content=f"普通问题 {index}", id=f"general-{index}")
                    for index in range(7)
                ],
                contract_human,
                contract_ai,
            ],
            "summary": "",
            "conversation_mode": "general",
        }

        condensed = await nodes.condense_memory(state)
        removed_ids = {message.id for message in condensed["messages"]}
        self.assertNotIn(contract_human.id, removed_ids)
        self.assertNotIn(contract_ai.id, removed_ids)
        response = nodes.chatbot(state)
        model_messages = llm_with_tools.invoke.call_args.args[0]
        self.assertTrue(all(message.content != contract_human.content for message in model_messages))
        self.assertTrue(all(message.content != contract_ai.content for message in model_messages))
        self.assertEqual(
            response["messages"][0].additional_kwargs["conversation_scope"],
            "general",
        )

    def test_contract_mode_filters_other_contract_history(self):
        llm_with_tools = Mock()
        llm_with_tools.invoke.return_value = AIMessage(content="")
        nodes = AgentNodes(llm=Mock(), llm_with_tools=llm_with_tools)
        state = {
            "messages": [
                HumanMessage(
                    content="合同 A 工资为 5000 元",
                    additional_kwargs={"conversation_scope": "contract:review-a"},
                ),
                HumanMessage(
                    content="合同 B 工资为 8000 元",
                    additional_kwargs={"conversation_scope": "contract:review-b"},
                ),
                HumanMessage(content="上传前的普通问题"),
            ],
            "conversation_mode": "contract_review",
            "active_review_id": "review-b",
        }

        nodes.chatbot(state)
        model_messages = llm_with_tools.invoke.call_args.args[0]
        self.assertTrue(all(message.content != "合同 A 工资为 5000 元" for message in model_messages))
        self.assertTrue(any(message.content == "合同 B 工资为 8000 元" for message in model_messages))

    def test_legacy_contract_checkpoint_filters_unscoped_history(self):
        llm_with_tools = Mock()
        llm_with_tools.invoke.return_value = AIMessage(content="")
        nodes = AgentNodes(llm=Mock(), llm_with_tools=llm_with_tools)
        state = {
            "messages": [
                HumanMessage(content="旧合同工资为 5000 元"),
                HumanMessage(
                    content="本轮合同问题",
                    additional_kwargs={"conversation_scope": "contract:review-b"},
                ),
                HumanMessage(
                    content="本轮普通问题",
                    additional_kwargs={"conversation_scope": "general"},
                ),
            ],
            "conversation_mode": "contract_review",
            "active_review_id": "review-b",
            "legacy_unscoped_messages": True,
        }

        nodes.chatbot(state)

        contents = [message.content for message in llm_with_tools.invoke.call_args.args[0]]
        self.assertNotIn("旧合同工资为 5000 元", contents)
        self.assertIn("本轮合同问题", contents)
        self.assertIn("本轮普通问题", contents)

    async def test_legacy_contract_checkpoint_drops_sensitive_summary(self):
        llm_with_tools = Mock()
        llm_with_tools.invoke.return_value = AIMessage(content="")
        nodes = AgentNodes(llm=Mock(), llm_with_tools=llm_with_tools)
        state = {
            "messages": [
                HumanMessage(
                    content="本轮合同问题",
                    additional_kwargs={"conversation_scope": "contract:review-b"},
                )
            ],
            "summary": "旧合同工资为 8000 元",
            "conversation_mode": "general",
            "legacy_unscoped_messages": True,
        }

        condensed = await nodes.condense_memory(state)
        self.assertEqual(condensed["summary"], "")
        nodes.chatbot(state)
        contents = [message.content for message in llm_with_tools.invoke.call_args.args[0]]
        self.assertTrue(all("8000" not in str(content) for content in contents))

    def test_answer_prompt_accepts_confirmed_user_corrections(self):
        self.assertIn("corrected", ANSWER_PROMPT)
        self.assertIn("supplemented", ANSWER_PROMPT)
        self.assertIn("effective_value", ANSWER_PROMPT)
        self.assertIn("not_applicable", ANSWER_PROMPT)

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
