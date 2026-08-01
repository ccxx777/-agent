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


def _is_contract_message(message: Any) -> bool:
    """判断消息是否来自合同上下文轮次。旧消息无标签时保持兼容。"""

    return _conversation_scope(message).startswith("contract:")


def _conversation_scope(message: Any) -> str:
    """读取消息范围标签；旧 checkpoint 消息返回空字符串。"""

    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    scope = additional_kwargs.get("conversation_scope", "")
    return scope if isinstance(scope, str) else ""


def _scope_for_state(state: AgentState) -> str:
    review_id = state.get("active_review_id", "")
    if state.get("conversation_mode") == "contract_review" and review_id:
        return f"contract:{review_id}"
    return state.get("conversation_mode", "general")


def _tag_message(message: Any, scope: str) -> Any:
    """为模型输出增加会话范围标签，以便后续模式切换时过滤。"""

    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs["conversation_scope"] = scope
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"additional_kwargs": additional_kwargs})
    return message


class AgentNodes:
    """持有共享模型并实现图节点。"""

    def __init__(self, *, llm: Any, llm_with_tools: Any) -> None:
        self._llm = llm
        self._llm_with_tools = llm_with_tools

    async def condense_memory(self, state: AgentState) -> dict:
        """消息超过六条时，只压缩非合同消息并移除对应的旧消息。"""
        messages = state["messages"]
        legacy_unscoped = bool(state.get("legacy_unscoped_messages"))
        safe_messages = [
            message
            for message in messages
            if not _is_contract_message(message)
            and (not legacy_unscoped or bool(_conversation_scope(message)))
        ]
        current_summary = "" if legacy_unscoped else state.get("summary", "")
        if len(safe_messages) <= 6:
            if legacy_unscoped and state.get("summary"):
                return {"summary": ""}
            return {}

        first_two = safe_messages[:2]
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
        legacy_unscoped = bool(state.get("legacy_unscoped_messages"))
        if state.get("conversation_mode", "general") == "contract_review":
            current_scope = _scope_for_state(state)
            messages = [
                message
                for message in messages
                if (
                    bool(_conversation_scope(message))
                    and (
                        not _is_contract_message(message)
                        or _conversation_scope(message) == current_scope
                    )
                )
                or (not legacy_unscoped and not _is_contract_message(message))
            ]
        else:
            messages = [
                message
                for message in messages
                if not _is_contract_message(message)
                and (not legacy_unscoped or bool(_conversation_scope(message)))
            ]
        summary = "" if legacy_unscoped else state.get("summary", "")
        system_content = build_chat_system_prompt(
            mode=state.get("conversation_mode", "general"),
            report_context=state.get("report_context", ""),
            contract_context=state.get("contract_context", ""),
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
        response = self._llm_with_tools.invoke(messages)
        return {"messages": [_tag_message(response, _scope_for_state(state))]}

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
        contract_context = state.get("contract_context", "") or state.get("report_context", "")
        retrieval_context = str(retrieval_result.get("context", ""))
        if not contract_context and not retrieval_context:
            return {
                "messages": [
                    AIMessage(content="抱歉，当前模式没有可用的、经过治理的参考资料。")
                ]
            }
        context = "\n\n".join(
            part for part in (contract_context, retrieval_context) if part
        ) or "(empty)"
        logger.info("[Funnel] q=%.50s | ctx_len=%d", query, len(context))
        result = await self._llm.ainvoke([
            {"role": "user", "content": ANSWER_PROMPT.format(context=context, query=query)}
        ])
        return {"messages": [_tag_message(AIMessage(content=result.content), _scope_for_state(state))]}
