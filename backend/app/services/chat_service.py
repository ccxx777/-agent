"""对话用例服务。

API 层只负责 HTTP 校验；本服务负责把用户问题转换为 LangGraph 输入并执行图。
``invoke`` 保留完整 Graph State，供 Eval API 读取 ToolMessage；``ask`` 提供普通
聊天端点所需的最终文本答案。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage

from app.services.report_chat_thread import report_chat_thread_id


def _is_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


class ChatReportNotFound(LookupError):
    """合同上下文问答引用的合同任务不存在或不属于当前用户。"""


class ChatReportSessionMismatch(PermissionError):
    """报告与当前会话不一致。"""


class ChatService:
    """统一封装 LangGraph 对话执行。

    普通问题和合同上传不再使用两套 thread。合同一旦绑定到某个
    ``session_id``，后续请求会把脱敏正文、结构化事实和风险报告作为同一份
    ``contract_context`` 写入该 session 的 checkpoint，用户可以先问问题再上传
    合同，也可以上传后继续追问；所有请求仍然先经过 user/session/review 归属校验。
    """

    _CONTRACT_CONTEXT_MAX_CHARS = 120_000

    def __init__(self, graph: Any, repository: Any | None = None) -> None:
        self._graph = graph
        self._repository = repository

    async def delete_report_thread(self, review_id: str) -> None:
        """删除旧版本报告专属 thread 的兼容清理入口。

        新版本合同上下文问答与普通聊天共用 ``session_id``，删除合同不应删除整个
        用户会话。因此这里仅清理历史版本留下的 ``contract-review:*`` thread，
        不触碰统一 session thread。
        """

        checkpointer = getattr(self._graph, "checkpointer", None)
        delete_thread = getattr(checkpointer, "adelete_thread", None)
        if not callable(delete_thread):
            raise RuntimeError("LangGraph checkpointer 不支持报告 thread 删除")
        await delete_thread(report_chat_thread_id(review_id))

    @staticmethod
    def _json_text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def _limit_context(cls, context: str) -> str:
        if len(context) <= cls._CONTRACT_CONTEXT_MAX_CHARS:
            return context
        # 合同正文位于上下文中部，事实和报告位于末尾。头尾各保留一段，避免
        # 长合同截断时把结构化事实/报告整个丢掉；同时明确提示正文不是完整正文。
        head_size = cls._CONTRACT_CONTEXT_MAX_CHARS // 2
        tail_size = cls._CONTRACT_CONTEXT_MAX_CHARS - head_size
        return (
            context[:head_size]
            + "\n\n[系统提示] 合同正文超过当前会话上下文上限，中间正文已截断；"
            "下方仍保留结构化事实与报告，回答时不得把截断当成合同没有相关条款。\n\n"
            + context[-tail_size:]
        )

    async def _contract_context(
        self,
        *,
        review_id: str | None,
        session_id: str,
        user_id: str,
    ) -> str:
        """构建统一合同上下文，只读取用户有权访问的脱敏数据。

        上下文分为三段：页级脱敏正文、事实/确认结果 JSON、风险报告 JSON。
        私有原始文件路径、原始二进制和未脱敏 OCR 图像永远不会进入 Prompt。
        """

        if not review_id or self._repository is None:
            raise ChatReportNotFound("合同问答必须绑定当前会话中的合同")

        get_task = getattr(self._repository, "get_task", None)
        if callable(get_task):
            task = await get_task(review_id, user_id)
            if not task:
                raise ChatReportNotFound("合同不存在或无权访问")
        else:
            # 兼容旧测试替身和尚未升级的装配层；生产仓储必须提供 get_task。
            task = {"review_id": review_id, "session_id": session_id, "pages": []}

        task_session_id = task.get("session_id")
        if not task_session_id or str(task_session_id) != str(session_id):
            raise ChatReportSessionMismatch("该报告不属于当前会话")

        get_report = getattr(self._repository, "get_report", None)
        report_row = await get_report(review_id, user_id) if callable(get_report) else None
        if report_row:
            report_session_id = report_row.get("session_id")
            if report_session_id and str(report_session_id) != str(session_id):
                raise ChatReportSessionMismatch("该报告不属于当前会话")

        pages = task.get("pages") or []
        page_lines = []
        for page in pages:
            page_no = page.get("page_no", "?")
            page_lines.append(f"[第 {page_no} 页]\n{str(page.get('text') or '')}")

        extraction = task.get("extraction_result") or {}
        confirmation = task.get("confirmation_result") or {}
        facts_payload = {
            "extracted_facts": extraction.get("facts", []) if isinstance(extraction, dict) else [],
            "confirmed_facts": confirmation.get("facts", []) if isinstance(confirmation, dict) else [],
            "unresolved_questions": (
                confirmation.get("unresolved_questions", [])
                if isinstance(confirmation, dict)
                else []
            ),
        }
        report = report_row.get("report") if report_row else None
        report_payload = report or {
            "status": "not_generated",
            "message": "合同审查报告尚未生成；只能回答当前已提取的正文和事实。",
        }
        context = "\n\n".join(
            [
                "合同元数据（只读）:\n"
                + self._json_text(
                    {
                        "review_id": str(review_id),
                        "filename": task.get("filename", ""),
                        "status": task.get("status", ""),
                        "extraction_status": task.get("extraction_status", ""),
                        "confirmation_status": task.get("confirmation_status", ""),
                    }
                ),
                "合同脱敏正文（按页）:\n"
                + ("\n\n".join(page_lines) if page_lines else "[尚无已保存的脱敏正文]"),
                "结构化事实 JSON（提取值与当前确认值分层保留）:\n"
                + self._json_text(facts_payload),
                "风险报告 JSON:\n" + self._json_text(report_payload),
            ]
        )
        return self._limit_context(context)

    async def _checkpoint_has_legacy_unscoped_messages(self, session_id: str) -> bool:
        """检测升级前的无标签历史，避免旧合同消息进入模型。

        旧版本把合同问答写入统一 session 时没有给消息打
        ``conversation_scope``，即使用户随后退出合同模式并清空了
        ``contract_context``，这些消息仍会留在 checkpoint。历史 API 仍可展示它们，
        但一旦发现任意无标签消息，后续模型输入会保守地过滤所有无标签历史；新写入
        的消息都带标签，因此不会影响新会话的正常上下文。
        """

        get_state = getattr(self._graph, "aget_state", None)
        if not callable(get_state):
            return False
        snapshot = await get_state({"configurable": {"thread_id": session_id}})
        values = getattr(snapshot, "values", None) or {}
        if bool(
            values.get("contract_context")
            or values.get("report_context")
            or values.get("active_review_id")
            or values.get("conversation_mode") == "contract_review"
        ):
            return True
        for message in values.get("messages", []) or []:
            if isinstance(message, dict):
                additional_kwargs = message.get("additional_kwargs", {}) or {}
            else:
                additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
            scope = additional_kwargs.get("conversation_scope")
            if not isinstance(scope, str) or not scope:
                return True
        return False

    async def _resolve_legacy_scope(
        self,
        *,
        session_id: str,
        user_id: str,
    ) -> bool:
        """读取或一次性计算会话 scope 迁移结果。"""

        repository = self._repository
        get_scope_state = getattr(repository, "get_conversation_scope_state", None)
        mark_scope_state = getattr(repository, "mark_conversation_scope_state", None)
        if callable(get_scope_state):
            scope_state = await get_scope_state(session_id, user_id)
            if scope_state:
                version = scope_state.get("conversation_scope_version")
                if version == 2:
                    return True
                if version == 1:
                    return False
                # 只有历史上绑定过合同的旧 session 才需要扫描；纯普通聊天保留
                # 原有无标签消息，避免升级后丢失连续记忆。
                if not scope_state.get("has_contract_context"):
                    if callable(mark_scope_state):
                        await mark_scope_state(session_id, user_id, 1)
                    return False

                legacy = await self._checkpoint_has_legacy_unscoped_messages(session_id)
                if callable(mark_scope_state):
                    await mark_scope_state(session_id, user_id, 2 if legacy else 1)
                return legacy

        # 兼容尚未接入迁移仓储的测试替身/旧装配层；生产仓储走上面的
        # 一次性持久化路径。
        return await self._checkpoint_has_legacy_unscoped_messages(session_id)

    async def invoke(
        self,
        *,
        query: str,
        session_id: str,
        user_id: str = "anonymous",
        mode: str = "general",
        review_id: str | None = None,
    ) -> dict[str, Any]:
        """执行一次统一会话调用；文字问题和合同上下文共享一个 session thread。"""
        if _is_uuid(user_id) and not _is_uuid(session_id):
            raise ValueError("session_id 必须是有效的 UUID")
        if mode == "contract_review" and not review_id:
            raise ChatReportNotFound("合同问答必须绑定当前会话中的合同")

        # 带 review_id 的请求自动进入合同上下文模式，即使旧前端仍传 mode=general。
        effective_mode = "contract_review" if review_id else mode
        contract_context = ""
        if review_id:
            contract_context = await self._contract_context(
                review_id=review_id,
                session_id=session_id,
                user_id=user_id,
            )
        message_scope = f"contract:{review_id}" if review_id else effective_mode
        input_state: dict[str, Any] = {
            "messages": [
                HumanMessage(
                    content=query,
                    additional_kwargs={"conversation_scope": message_scope},
                )
            ],
        }
        if (
            self._repository is not None
            and hasattr(self._repository, "ensure_session")
            and _is_uuid(session_id)
            and _is_uuid(user_id)
        ):
            await self._repository.ensure_session(session_id, user_id)
        if _is_uuid(session_id) and _is_uuid(user_id):
            if await self._resolve_legacy_scope(session_id=session_id, user_id=user_id):
                input_state["legacy_unscoped_messages"] = True
                # 旧摘要可能包含未打 scope 的合同事实；先清空 checkpoint 中的摘要，
                # 再由节点只基于已标记的安全消息重新压缩。
                input_state["summary"] = ""
        # 每轮都显式写入模式，避免从合同问答切回普通聊天时让旧的
        # ``conversation_mode=contract_review`` 残留在 LangGraph checkpoint。
        input_state["conversation_mode"] = effective_mode
        if review_id:
            input_state["active_review_id"] = review_id
            # 合同正文/事实/报告属于敏感上下文；清空旧的通用摘要。之后
            # condense_memory 只会压缩非合同消息，普通模式也会过滤合同消息。
            input_state["summary"] = ""
        else:
            # 显式清除上一次合同上下文，避免删除/切换合同后旧正文继续留在 checkpoint。
            input_state["active_review_id"] = ""
        input_state["contract_context"] = contract_context
        input_state["report_context"] = ""  # 清理旧版本字段，兼容历史 checkpoint
        # 合同问答与普通文字问答共享同一个用户 session；报告不再创建第二条会话历史。
        thread_id = session_id
        return await self._graph.ainvoke(
            input_state,
            {"configurable": {"thread_id": thread_id, "user_id": user_id}},
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
