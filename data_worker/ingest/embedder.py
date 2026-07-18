"""文档批量向量化客户端。

通过独立 BGE-M3 HTTP 服务生成 Dense 与 Sparse 向量。默认每批两个 Chunk，
保持原服务器的低并发策略；该模块不负责重试、召回或 Qdrant 写入。
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)


class DocumentEmbedder:
    """同步调用 Embedding Service 的入库客户端。"""

    def __init__(self, *, endpoint: str, batch_size: int = 2) -> None:
        self._endpoint = endpoint
        self._batch_size = batch_size

    def embed(self, chunks: list[str]) -> tuple[list[list[float]], list[dict]]:
        """按原顺序返回 Dense 与 Sparse 向量列表。"""
        dense_vectors: list[list[float]] = []
        sparse_vectors: list[dict] = []
        total_batches = (len(chunks) + self._batch_size - 1) // self._batch_size

        for offset in range(0, len(chunks), self._batch_size):
            batch = chunks[offset : offset + self._batch_size]
            batch_number = offset // self._batch_size + 1
            logger.info("正在向量化批次 %d/%d (%d chunks)...", batch_number, total_batches, len(batch))
            response = httpx.post(
                self._endpoint,
                json={"texts": batch, "dense": True, "sparse": True},
                timeout=None,
            )
            response.raise_for_status()
            payload = response.json()
            dense_vectors.extend(payload["dense"])
            sparse_vectors.extend(payload.get("sparse", []))
            time.sleep(0.1)

        return dense_vectors, sparse_vectors
