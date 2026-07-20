"""RAG 检索用例服务。

这是 Agent 与自研 Cascade Funnel 之间唯一的业务入口。它负责：
1. 调用 Embedding Service 获取查询向量；
2. 把原始查询和向量交给当前 Funnel；
3. 将已排好序的结果转换成稳定 ``RetrievalPayload``。

Collection由装配层注入，因此离线建好的v2 Collection可通过环境变量切换，
无需改代码或覆盖旧库。
"""

from __future__ import annotations

from app.components.retriever.qdrant.v2_0_0.main import get_final_funnel_top3
from app.infrastructure.embedding_client import EmbeddingClient
from app.infrastructure.qdrant import QdrantGateway
from app.schemas.retrieval import RetrievalPayload, build_retrieval_payload


class RetrievalService:
    """编排查询向量化、冻结召回器和结果适配器。"""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        qdrant: QdrantGateway,
        reranker_model: str,
        reranker_api_url: str,
        reranker_api_key: str,
        collection_name: str = "rag_chunks",
    ) -> None:
        self._embedding_client = embedding_client
        self._qdrant = qdrant
        self._reranker_model = reranker_model
        self._reranker_api_url = reranker_api_url
        self._reranker_api_key = reranker_api_key
        self._collection_name = collection_name

    async def retrieve(self, query: str) -> RetrievalPayload:
        """返回 Funnel 最终排序后的稳定检索结果。

        ``documents`` 的列表顺序就是最终 Rank；这里不会重排、过滤或重新计算
        分数。``contexts`` 与 Agent 实际生成答案时使用的上下文完全一致。
        """
        dense_vector, sparse_vector = await self._embedding_client.embed_query(query)
        ranked_hits = await get_final_funnel_top3(
            query,
            dense_vec=dense_vector,
            sparse_dict=sparse_vector,
            qdrant_url=self._qdrant.url,
            reranker_model=self._reranker_model,
            reranker_api_url=self._reranker_api_url,
            reranker_api_key=self._reranker_api_key,
            collection_name=self._collection_name,
        )
        return build_retrieval_payload(ranked_hits)
