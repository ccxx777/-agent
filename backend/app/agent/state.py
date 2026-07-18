"""LangGraph 状态定义。

状态文件必须保持纯净：只描述图中流动的数据，不创建模型、工具或数据库连接。
``messages`` 使用 LangGraph 的追加归并器，``summary`` 保存压缩后的长期记忆。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """知识库 Agent 在节点之间传递的完整状态。"""

    messages: Annotated[list[AnyMessage], add_messages]
    summary: str
