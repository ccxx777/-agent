"""对话用例服务。

API 层只负责 HTTP 校验；本服务负责把用户问题转换为 LangGraph 输入并执行图。
``invoke`` 保留完整 Graph State，供 Eval API 读取 ToolMessage；``ask`` 提供普通
聊天端点所需的最终文本答案。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage


class ChatService:
    """统一封装 LangGraph 对话执行。"""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def invoke(
        self,
        *,
        query: str,
        session_id: str,
        user_id: str = "anonymous",
    ) -> dict[str, Any]:
        """执行一次带会话标识的 Agent 调用并返回完整状态。"""
        return await self._graph.ainvoke(
            {"messages": [HumanMessage(content=query)]},
            {"configurable": {"thread_id": session_id, "user_id": user_id}},
        )

    async def ask(self, *, query: str, session_id: str, user_id: str) -> str:
        """执行普通聊天并提取最后一条消息作为答案。"""
        result = await self.invoke(query=query, session_id=session_id, user_id=user_id)
        messages = result.get("messages", [])
        return str(messages[-1].content) if messages else ""
