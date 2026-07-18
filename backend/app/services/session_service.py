"""会话历史查询服务。

历史状态仍由 LangGraph PostgresSaver 持久化。本服务只负责从 Graph Snapshot
读取状态并转换成稳定的 API 数据，不自行维护第二套消息存储。
"""

from __future__ import annotations

from typing import Any


class SessionService:
    """读取指定 LangGraph thread 的对话历史和摘要。"""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def get_history(self, session_id: str) -> dict:
        """返回 human/ai 消息和当前摘要；状态不存在时返回空结果。"""
        state = await self._graph.aget_state(
            {"configurable": {"thread_id": session_id.strip()}}
        )
        if state is None or state.values is None:
            return {"messages": [], "summary": ""}

        messages: list[dict[str, str]] = []
        for message in state.values.get("messages", []):
            type_name = message.__class__.__name__
            if type_name == "HumanMessage":
                role = "human"
            elif type_name == "AIMessage":
                role = "ai"
            else:
                continue
            content = getattr(message, "content", "")
            if content:
                messages.append({"role": role, "content": str(content)})

        return {
            "messages": messages,
            "summary": str(state.values.get("summary", "")),
        }
