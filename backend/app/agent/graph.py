"""LangGraph 拓扑组装。

本模块只连接 State、Nodes、Tools 和 Postgres Checkpointer，不再实现节点业务、
创建外部客户端或读取环境变量。图拓扑保持原样：
``START → condense_memory → chatbot → tools → generate_answer → END``。
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from langchain_core.messages import ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.nodes import AgentNodes
from app.agent.state import AgentState
from app.agent.tools import create_retrieval_tools
from app.infrastructure.model_provider import ModelProvider
from app.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


class ModeAwareToolNode:
    """在工具真正执行前按会话模式做服务端 allowlist 校验。"""

    _ALLOWED_TOOLS: ClassVar[dict[str, set[str]]] = {
        "general": {"search_knowledge_base"},
        "legal": {"search_legal_knowledge_base"},
        "contract_review": set(),
    }

    def __init__(self, tools: list[Any]) -> None:
        self._tool_node = ToolNode(tools)

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        mode = state.get("conversation_mode", "general")
        allowed = self._ALLOWED_TOOLS.get(mode, self._ALLOWED_TOOLS["general"])
        last_message = state["messages"][-1]
        calls = getattr(last_message, "tool_calls", []) or []
        blocked = [call for call in calls if call.get("name") not in allowed]
        if blocked:
            return {
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "contexts": [],
                                "documents": [],
                                "context": "",
                                "error": (
                                    "tool_not_allowed_for_mode"
                                    if call.get("name") not in allowed
                                    else "mixed_tool_batch_rejected"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        tool_call_id=str(call.get("id") or "blocked-tool"),
                    )
                    for call in calls
                ]
            }
        return await self._tool_node.ainvoke(state)


def _route_after_chat(state: AgentState) -> str:
    """普通模式无工具时结束；法律/报告模式进入受约束的最终回答节点。"""

    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    if state.get("conversation_mode") in {"legal", "contract_review"}:
        return "generate_answer"
    return "finish"


def get_compiled_graph(
    conn_pool: Any,
    *,
    retrieval_service: RetrievalService,
    model_provider: ModelProvider,
    legal_retrieval_service: Any | None = None,
) -> Any:
    """组装并编译 Agent Graph。

    所有外部依赖都由应用装配层传入。返回的图使用 PostgreSQL Checkpointer，
    因此 Chat API 与 Eval API 共享相同的执行语义。
    """
    llm = model_provider.create_chat_model()
    tools = create_retrieval_tools(retrieval_service, legal_retrieval_service)
    llm_with_tools = llm.bind_tools(tools)
    nodes = AgentNodes(llm=llm, llm_with_tools=llm_with_tools)

    workflow = StateGraph(AgentState)
    workflow.add_node("condense_memory", nodes.condense_memory)
    workflow.add_node("chatbot", nodes.chatbot)
    workflow.add_node("tools", ModeAwareToolNode(tools))
    workflow.add_node("generate_answer", nodes.generate_answer)

    workflow.add_edge(START, "condense_memory")
    workflow.add_edge("condense_memory", "chatbot")
    workflow.add_conditional_edges(
        "chatbot",
        _route_after_chat,
        {"tools": "tools", "generate_answer": "generate_answer", "finish": END},
    )
    workflow.add_edge("tools", "generate_answer")
    workflow.add_edge("generate_answer", END)

    compiled = workflow.compile(checkpointer=AsyncPostgresSaver(conn_pool))
    logger.info("LangGraph compiled (condense_memory → Cascade Funnel)")
    return compiled
