"""Agent 节点实现。

每个方法对应 LangGraph 中的一个节点：记忆压缩、工具决策和最终答案生成。
节点只处理 ``AgentState``，模型与绑定工具由构造函数注入，便于单元测试并
避免节点在导入时读取环境变量。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.prompts import ANSWER_PROMPT, SUMMARY_PROMPT, build_chat_system_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


class AgentNodes:
    """持有共享模型并实现图节点。"""

    def __init__(self, *, llm: Any, llm_with_tools: Any) -> None:
        self._llm = llm
        self._llm_with_tools = llm_with_tools

    async def condense_memory(self, state: AgentState) -> dict:
        """消息超过六条时，将最旧一问一答融合进摘要并移除原消息。"""
        messages = state["messages"]
        current_summary = state.get("summary", "")
        if len(messages) <= 6:
            return {}

        first_two = messages[:2]
        dialog_lines = [
            f"{'用户' if isinstance(message, HumanMessage) else 'AI'}: {message.content[:300]}"
            for message in first_two
        ]
        result = await self._llm.ainvoke([
            {
                "role": "user",
                "content": SUMMARY_PROMPT.format(
                    summary=current_summary or "（无）",
                    messages="\n".join(dialog_lines),
                ),
            }
        ])
        new_summary = result.content.strip()
        logger.info("记忆压缩完成: %d chars", len(new_summary))
        return {
            "summary": new_summary,
            "messages": [RemoveMessage(id=message.id) for message in first_two],
        }

    def chatbot(self, state: AgentState) -> dict:
        """注入系统提示和历史摘要，让模型决定是否调用检索工具。"""
        messages = list(state["messages"])
        summary = state.get("summary", "")
        system_content = build_chat_system_prompt(
            mode=state.get("conversation_mode", "general"),
            report_context=state.get("report_context", ""),
        )
        if summary:
            system_content = f"## 历史对话摘要（长期记忆）\n{summary}\n\n{system_content}"
        system_index = next(
            (index for index, message in enumerate(messages) if isinstance(message, SystemMessage)),
            None,
        )
        if system_index is None:
            messages = [SystemMessage(content=system_content), *messages]
        else:
            # 系统提示不写回历史消息，但每轮都按当前 mode/report_context 更新。
            messages[system_index] = SystemMessage(content=system_content)
        return {"messages": [self._llm_with_tools.invoke(messages)]}

    async def generate_answer(self, state: AgentState) -> dict:
        """读取 ToolMessage 的结构化上下文并生成有引用的最终答案。"""
        last_message = state["messages"][-1]
        retrieval_result: dict[str, Any] = {}
        if isinstance(last_message, ToolMessage):
            try:
                retrieval_result = json.loads(last_message.content)
            except (json.JSONDecodeError, TypeError):
                retrieval_result = {}

        query = next(
            (
                str(message.content)
                for message in reversed(state["messages"])
                if isinstance(message, HumanMessage)
            ),
            "",
        )
        report_context = state.get("report_context", "")
        retrieval_context = str(retrieval_result.get("context", ""))
        if not report_context and not retrieval_context:
            return {
                "messages": [
                    AIMessage(content="抱歉，当前模式没有可用的、经过治理的参考资料。")
                ]
            }
        context = "\n\n".join(
            part for part in (report_context, retrieval_context) if part
        ) or "(empty)"
        logger.info("[Funnel] q=%.50s | ctx_len=%d", query, len(context))
        result = await self._llm.ainvoke([
            {"role": "user", "content": ANSWER_PROMPT.format(context=context, query=query)}
        ])
        return {"messages": [AIMessage(content=result.content)]}
