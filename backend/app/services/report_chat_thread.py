"""旧版本合同报告 thread 标识的兼容映射。

当前合同问答与普通文字问答统一使用 ``session_id``。该函数只用于读取和清理
迁移前已经创建的 ``contract-review:<review_id>`` checkpoint，不再用于新请求。
"""

from __future__ import annotations


def report_chat_thread_id(review_id: str) -> str:
    """根据 review_id 返回稳定且独立于普通 session 的报告 thread。"""

    normalized = str(review_id).strip()
    if not normalized:
        raise ValueError("review_id 不能为空")
    return f"contract-review:{normalized}"
