"""对话用例服务。

API 层只负责 HTTP 校验；本服务负责把用户问题转换为 LangGraph 输入并执行图。
``invoke`` 保留完整 Graph State，供 Eval API 读取 ToolMessage；``ask`` 提供普通
聊天端点所需的最终文本答案。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


class ChatReportNotFound(LookupError):
    """报告问答引用的报告不存在或不属于当前用户。"""


class ChatReportSessionMismatch(PermissionError):
    """报告与当前会话不一致。"""


class ChatService:
    """统一封装 LangGraph 对话执行。"""

    def __init__(self, graph: Any, repository: Any | None = None) -> None:
        self._graph = graph
        self._repository = repository

    async def _report_context(
        self,
        *,
        review_id: str | None,
        session_id: str,
        user_id: str,
    ) -> str:
        """读取已持久化报告的只读摘要，禁止把原始合同注入通用会话。"""

        if not review_id or self._repository is None or not hasattr(self._repository, "get_report"):
            raise ChatReportNotFound("报告问答必须绑定已持久化报告")
        report_row = await self._repository.get_report(review_id, user_id)
        if not report_row:
            raise ChatReportNotFound("报告不存在或无权访问")
        report_session_id = report_row.get("session_id")
        if not report_session_id or str(report_session_id) != str(session_id):
            raise ChatReportSessionMismatch("该报告不属于当前会话")

        report = report_row.get("report") or {}
        findings = report.get("findings") or []
        finding_lines = []
        for finding in findings[:20]:
            evidence = finding.get("evidence") or []
            quote = str(evidence[0].get("quote", ""))[:500] if evidence else ""
            finding_lines.append(
                "- {title} | 风险={risk} | {summary} | 证据={quote}".format(
                    title=finding.get("title", ""),
                    risk=finding.get("risk_level", ""),
                    summary=str(finding.get("summary", ""))[:600],
                    quote=quote,
                )
            )
        sources = report.get("legal_sources") or []
        source_lines = [
            "- {title}（{label}）: {quote}".format(
                title=source.get("title", ""),
                label=source.get("citation_label", ""),
                quote=str(source.get("quote", ""))[:500],
            )
            for source in sources[:10]
        ]
        pending = report.get("pending_questions") or []
        return "\n".join(
            [
                f"报告版本：{report.get('report_version', report_row.get('report_version', 1))}",
                f"审查范围：{report.get('scope', '')}",
                "风险发现：",
                *(finding_lines or ["- 当前报告没有结构化风险发现"]),
                "待确认问题：",
                *(f"- {item}" for item in pending[:20]),
                "法律依据：",
                *(source_lines or ["- 当前报告没有可展示的法律依据"]),
                "报告免责声明：" + str(report.get("disclaimer", "")),
            ]
        )

    async def invoke(
        self,
        *,
        query: str,
        session_id: str,
        user_id: str = "anonymous",
        mode: str = "general",
        review_id: str | None = None,
    ) -> dict[str, Any]:
        """执行一次带会话、模式和可选报告上下文的 Agent 调用。"""
        if _is_uuid(user_id) and not _is_uuid(session_id):
            raise ValueError("session_id 必须是有效的 UUID")
        if mode == "contract_review" and not review_id:
            raise ChatReportNotFound("报告问答必须绑定 review_id")
        report_context = ""
        if mode == "contract_review":
            report_context = await self._report_context(
                review_id=review_id,
                session_id=session_id,
                user_id=user_id,
            )
        input_state: dict[str, Any] = {
            "messages": [HumanMessage(content=query)],
        }
        if (
            self._repository is not None
            and hasattr(self._repository, "ensure_session")
            and _is_uuid(session_id)
            and _is_uuid(user_id)
        ):
            await self._repository.ensure_session(session_id, user_id)
        if mode != "general":
            input_state["conversation_mode"] = mode
        if review_id:
            input_state["active_review_id"] = review_id
        if mode == "contract_review":
            input_state["report_context"] = report_context
        return await self._graph.ainvoke(
            input_state,
            {"configurable": {"thread_id": session_id, "user_id": user_id}},
        )

    async def ask(
        self,
        *,
        query: str,
        session_id: str,
        user_id: str,
        mode: str = "general",
        review_id: str | None = None,
    ) -> str:
        """执行会话聊天并提取最后一条消息作为答案。"""
        result = await self.invoke(
            query=query,
            session_id=session_id,
            user_id=user_id,
            mode=mode,
            review_id=review_id,
        )
        messages = result.get("messages", [])
        return str(messages[-1].content) if messages else ""
