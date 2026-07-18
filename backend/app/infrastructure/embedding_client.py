"""Embedding Service 的 HTTP 客户端。

该客户端只负责协议转换：文本列表进、Dense/Sparse 向量出。它不负责召回、
融合或重排，因此修改本文件不会改变 Cascade Funnel 的排序规则。
"""

from __future__ import annotations

import httpx


class EmbeddingClient:
    """调用独立 BGE-M3 服务的轻量客户端。"""

    def __init__(self, endpoint: str, timeout: float = 15.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout

    async def embed_query(self, text: str) -> tuple[list[float], dict[int, float]]:
        """为单个查询生成 Dense 与 Sparse 表示。

        返回值与冻结召回器 ``get_final_funnel_top3`` 的输入格式保持一致。
        HTTP 错误直接向上抛出，由 API 边界统一转换为请求失败。
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._endpoint,
                json={"texts": [text], "dense": True, "sparse": True},
            )
        response.raise_for_status()
        payload = response.json()
        return payload["dense"][0], (payload.get("sparse") or [{}])[0]
