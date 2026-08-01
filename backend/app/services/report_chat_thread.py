"""合同报告问答的 LangGraph thread 标识。

普通对话使用用户会话 UUID；报告问答必须使用报告级 thread，避免同一普通
session 中的多份合同共享 checkpoint。该函数只做稳定的命名空间映射，不创建
数据库或图客户端。
"""

from __future__ import annotations


def report_chat_thread_id(review_id: str) -> str:
    """根据 review_id 返回稳定且独立于普通 session 的报告 thread。"""

    normalized = str(review_id).strip()
    if not normalized:
        raise ValueError("review_id 不能为空")
    return f"contract-review:{normalized}"
