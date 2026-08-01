"""会话历史查询服务。

历史状态仍由 LangGraph PostgresSaver 持久化。本服务只负责从 Graph Snapshot
读取状态并转换成稳定的 API 数据，不自行维护第二套消息存储。
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.contract_review_repository import SessionOwnershipError
from app.services.report_chat_thread import report_chat_thread_id


class SessionService:
    """读取指定 LangGraph thread 的对话历史和摘要。"""

    def __init__(self, graph: Any, repository: Any | None = None) -> None:
        self._graph = graph
        self._repository = repository

    async def _read_thread(self, thread_id: str) -> dict:
        state = await self._graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        if state is None or state.values is None:
            return {"messages": [], "summary": ""}

        messages: list[dict[str, str]] = []
        for message in state.values.get("messages", []):
            type_name = message.__class__.__name__
            if type_name == "HumanMessage":
                role = "user"
            elif type_name == "AIMessage":
                role = "assistant"
            else:
                continue
            content = getattr(message, "content", "")
            if content:
                messages.append({"role": role, "content": str(content)})

        return {
            "messages": messages,
            "summary": str(state.values.get("summary", "")),
        }

    async def get_history(self, session_id: str, user_id: str | None = None) -> dict:
        """返回 human/ai 消息和当前摘要；状态不存在时返回空结果。"""
        if self._repository is not None and user_id and hasattr(self._repository, "get_session_owner"):
            owner = await self._repository.get_session_owner(session_id.strip())
            if owner is None:
                return {"messages": [], "summary": ""}
            if owner != str(user_id):
                raise SessionOwnershipError(session_id)
        return await self._read_thread(session_id.strip())

    async def get_report_history(self, review_id: str, user_id: str) -> dict | None:
        """读取绑定合同所在的统一 session 历史。

        新版本合同上下文问答不再创建 ``contract-review:*`` 专属 thread，而是与上传前
        的文字问答共享同一个 ``session_id``。如果读取到旧报告记录但统一 thread
        还没有消息，则回退读取旧 thread，保证历史数据可恢复。
        """

        if self._repository is None or not hasattr(self._repository, "get_report"):
            return None
        report = await self._repository.get_report(review_id, user_id)
        if not report:
            return None
        session_id = report.get("session_id")
        if session_id:
            current = await self.get_history(str(session_id), user_id)
            if current.get("messages") or current.get("summary"):
                return current
        # 兼容迁移前已经产生的报告专属对话历史。
        return await self._read_thread(report_chat_thread_id(review_id))
