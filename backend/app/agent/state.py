"""LangGraph 状态定义。

状态文件必须保持纯净：只描述图中流动的数据，不创建模型、工具或数据库连接。
``messages`` 使用 LangGraph 的追加归并器，``summary`` 只从非合同消息压缩得到；
合同消息会在普通/法律模式的模型输入中过滤，避免敏感事实跨模式泄漏。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """统一会话 Agent 在节点之间传递的状态。

    ``contract_context`` 会随着同一个 ``session_id`` 持久化到 LangGraph
    checkpoint。它可以在用户先提问、后上传合同时被写入，也可以在用户清除
    当前合同上下文时显式写成空字符串。上下文只包含脱敏正文、结构化事实和
    已持久化报告，不保存原始文件路径或未脱敏内容。
    """

    messages: Annotated[list[AnyMessage], add_messages]
    summary: str
    conversation_mode: str
    active_review_id: str
    # 升级前未带 conversation_scope 的历史消息只保留给历史 API 展示，不送入模型。
    legacy_unscoped_messages: bool
    # 统一会话上下文；report_context 保留用于读取旧 checkpoint。
    contract_context: str
    report_context: str
