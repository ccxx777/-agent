"""Qdrant 文档块写入器。

Writer 负责集合存在性、Point/Payload 构造、批量 Upsert，以及内容相同但路径
改变时的 ``source`` 同步。它不执行文档加载、分块或 Embedding。
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

logger = logging.getLogger(__name__)


class QdrantWriter:
    """把已向量化的 Chunk 写入指定 Qdrant 集合。"""

    def __init__(self, *, url: str, collection_name: str, vector_dim: int) -> None:
        self._client = QdrantClient(url=url)
        self._collection_name = collection_name
        self._vector_dim = vector_dim

    def ensure_collection(self) -> None:
        """集合不存在时创建 Dense Cosine 集合；存在时不修改其配置。"""
        names = [item.name for item in self._client.get_collections().collections]
        if self._collection_name in names:
            return
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=self._vector_dim,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection: %s", self._collection_name)

    def sync_source(self, sha256: str, new_path: str) -> int:
        """按 SHA256 更新已有 Point 的 source，不重新向量化。"""
        point_ids = []
        offset = None
        while True:
            batch, offset = self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="sha256",
                            match=qdrant_models.MatchValue(value=sha256),
                        )
                    ]
                ),
                limit=200,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.extend(point.id for point in batch)
            if offset is None:
                break
        if not point_ids:
            return 0
        self._client.set_payload(
            collection_name=self._collection_name,
            payload={"source": new_path},
            points=point_ids,
        )
        return len(point_ids)

    def write(
        self,
        *,
        source: str,
        sha256: str,
        chunks: list[str],
        dense_vectors: list[list[float]],
        sparse_vectors: list[dict],
    ) -> tuple[str, str, int]:
        """构造并 Upsert Points，返回 ``doc_id``、标题和写入数量。

        Point ID、Chunk ID 和 Payload 字段沿用原 Sentinel，保证已有数据和召回器
        可以继续识别同一份文档。
        """
        self.ensure_collection()
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, source))
        normalized = source.replace("\\", "/")
        title = normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "unknown"

        points = []
        for index, (vector, chunk) in enumerate(zip(dense_vectors, chunks)):
            chunk_id = f"{doc_id}/chunk_{index:04d}"
            payload = {
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "chunk_text": chunk,
                "title": title,
                "source": source,
                "sha256": sha256,
                "user_id": "sentinel",
            }
            if index < len(sparse_vectors) and sparse_vectors[index]:
                sparse = sparse_vectors[index]
                payload["sparse_indices"] = [int(key) for key in sparse]
                payload["sparse_values"] = [float(value) for value in sparse.values()]
            points.append(
                qdrant_models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id)),
                    vector=vector,
                    payload=payload,
                )
            )

        self._client.upsert(collection_name=self._collection_name, points=points)
        return doc_id, title, len(points)
