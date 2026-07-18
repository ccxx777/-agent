"""LangGraph 拓扑组装。

本模块只连接 State、Nodes、Tools 和 Postgres Checkpointer，不再实现节点业务、
创建外部客户端或读取环境变量。图拓扑保持原样：
``START → condense_memory → chatbot → tools → generate_answer → END``。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.nodes import AgentNodes
from app.agent.state import AgentState
from app.agent.tools import create_retrieval_tools
from app.infrastructure.model_provider import ModelProvider
from app.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


def get_compiled_graph(
    conn_pool: Any,
    *,
    retrieval_service: RetrievalService,
    model_provider: ModelProvider,
) -> Any:
    """组装并编译 Agent Graph。

    所有外部依赖都由应用装配层传入。返回的图使用 PostgreSQL Checkpointer，
    因此 Chat API 与 Eval API 共享相同的执行语义。
    """
    llm = model_provider.create_chat_model()
    tools = create_retrieval_tools(retrieval_service)
    llm_with_tools = llm.bind_tools(tools)
    nodes = AgentNodes(llm=llm, llm_with_tools=llm_with_tools)

    workflow = StateGraph(AgentState)
    workflow.add_node("condense_memory", nodes.condense_memory)
    workflow.add_node("chatbot", nodes.chatbot)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("generate_answer", nodes.generate_answer)

    workflow.add_edge(START, "condense_memory")
    workflow.add_edge("condense_memory", "chatbot")
    workflow.add_conditional_edges("chatbot", tools_condition)
    workflow.add_edge("tools", "generate_answer")
    workflow.add_edge("generate_answer", END)

    compiled = workflow.compile(checkpointer=AsyncPostgresSaver(conn_pool))
    logger.info("LangGraph compiled (condense_memory → Cascade Funnel)")
    return compiled
