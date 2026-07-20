"""retriever.qdrant:v2_0_0 — 企业级串联漏斗架构 (Cascade Funnel)。

三层漏斗:
  1. 召回层 (Recall) — Dense + Sparse + Fulltext 并发 Top-10 → ID 去重
  2. 粗排层 (Coarse Rank) — 缺省惩罚动态归一化 + 语义保底防误杀 → Top-10
  3. 精排层 (Fine Rank) — 硅基流动 Reranker 交叉编码 → Top-3 (异常降级至粗排)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.services.query_specificity import (
    bm25_query_tokens,
    calculate_query_specificity_details,
    sparse_token_id,
)

try:
    from log import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("retriever.qdrant")

COLLECTION_NAME = "rag_chunks"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_EMBED_URL = "http://embedding_service:8001/embed"
RRF_K = 60
DENSE_VECTOR_NAME = "dense"
BGE_SPARSE_VECTOR_NAME = "bge_m3_sparse"
BM25_EN_VECTOR_NAME = "bm25_word"
BM25_ZH_VECTOR_NAME = "bm25_zh"


def run(inputs: Dict[str, Any]) -> Dict[str, Any]:
    query = str(inputs.get("query", ""))
    top_k = int(inputs.get("top_k", 10))
    qdrant_url = str(inputs.get("qdrant_url", DEFAULT_QDRANT_URL))
    embed_url = str(inputs.get("embedding_url", DEFAULT_EMBED_URL))
    search_types = inputs.get("search_types", ["dense", "sparse", "fulltext"])

    if not query:
        return {"code": 400, "msg": "query 不能为空", "data": {}}

    t0 = time.time()

    # ── 获取 embedding ──
    try:
        resp = httpx.post(embed_url, json={"texts": [query], "dense": True, "sparse": True}, timeout=30)
        resp.raise_for_status()
        emb = resp.json()
        dense_vec = emb["dense"][0]
        sparse_dict = emb.get("sparse", [{}])[0] if emb.get("sparse") else {}
    except Exception as e:
        logger.error("Embedding failed: %s", e)
        return {"code": 500, "msg": f"embedding failed: {e}", "data": {}}

    client = QdrantClient(url=qdrant_url)
    all_docs: Dict[str, dict] = {}

    def _add(hits, path_name: str):
        for rank, hit in enumerate(hits, 1):
            pid = str(hit.id) if hasattr(hit, 'id') else str(hit.get('id', ''))
            if pid not in all_docs:
                p = hit.payload if hasattr(hit, 'payload') else hit.get('payload', {})
                all_docs[pid] = {
                    "doc_id": p.get("doc_id", ""), "chunk_id": p.get("chunk_id", ""),
                    "chunk_text": (p.get("chunk_text", "") or "")[:300],
                    "title": p.get("title", ""), "source": p.get("source", ""),
                    "scores_per_path": {},
                }
            all_docs[pid]["scores_per_path"][path_name] = 1.0 / (RRF_K + rank)

    # Dense
    if "dense" in search_types:
        try:
            _add(client.search(collection_name=COLLECTION_NAME, query_vector=dense_vec, limit=top_k * 2), "dense")
        except Exception as e:
            logger.warning("Dense search failed: %s", e)

    # Sparse
    if "sparse" in search_types and sparse_dict:
        try:
            _add(_sparse_search(client, sparse_dict, top_k * 2), "sparse")
        except Exception as e:
            logger.warning("Sparse search failed: %s", e)

    # Fulltext
    if "fulltext" in search_types:
        try:
            _add(_fulltext_search(client, query, top_k * 2), "fulltext")
        except Exception as e:
            logger.warning("Fulltext search failed: %s", e)

    # RRF 融合
    for d in all_docs.values():
        d["score"] = sum(d["scores_per_path"].values())

    ranked = sorted(all_docs.values(), key=lambda d: d["score"], reverse=True)[:top_k]
    elapsed = (time.time() - t0) * 1000

    logger.info("Recall '%s': dense=%d sparse=%d ft=%d → %d docs (%.1fms)",
                query[:30],
                sum(1 for d in all_docs.values() if "dense" in d.get("scores_per_path", {})),
                sum(1 for d in all_docs.values() if "sparse" in d.get("scores_per_path", {})),
                sum(1 for d in all_docs.values() if "fulltext" in d.get("scores_per_path", {})),
                len(ranked), elapsed)

    return {"code": 200, "msg": "ok", "data": {
        "query": query, "docs": ranked, "elapsed_ms": elapsed, "total": len(ranked),
    }}


def _sparse_search(client: QdrantClient, query_sparse: Dict[int, float], limit: int = 20) -> list:
    all_points, offset = [], None
    while True:
        batch, offset = client.scroll(collection_name=COLLECTION_NAME, limit=200,
                                       offset=offset, with_payload=True, with_vectors=False)
        all_points.extend(batch)
        if offset is None:
            break

    scored = []
    for pt in all_points:
        p = pt.payload or {}
        indices = p.get("sparse_indices", [])
        values = p.get("sparse_values", [])
        s = sum(query_sparse.get(int(idx), 0) * float(val) for idx, val in zip(indices, values))
        if s > 0:
            scored.append((s, pt))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


def _fulltext_search(client: QdrantClient, query: str, limit: int = 20) -> list:
    keywords = [w for w in query[:100].split() if len(w) >= 1]
    if not keywords:
        return []

    all_ids: set = set()
    for kw in keywords[:5]:
        try:
            batch, _ = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=qm.Filter(must=[qm.FieldCondition(key="chunk_text", match=qm.MatchText(text=kw))]),
                limit=limit, with_payload=True, with_vectors=False,
            )
            for p in batch:
                all_ids.add(p.id)
        except Exception:
            continue

    if not all_ids:
        return []
    return client.retrieve(collection_name=COLLECTION_NAME, ids=list(all_ids)[:limit])


# ═════════════════════════════════════════════════════════════════
# 粗排：缺省惩罚的动态归一化融合 (Dynamic Normalized Fusion)
# ═════════════════════════════════════════════════════════════════

def calculate_query_specificity(query: str) -> float:
    """兼容旧测试/调用方；实际实现位于Service层的双语分析器。"""

    return calculate_query_specificity_details(query).specificity


def normalize(scored_hits: list) -> tuple[dict, dict]:
    """Min-max 归一化 → [0, 1]。

    Args:
        scored_hits: list of (score, point) tuples

    Returns:
        ({uuid: norm_score}, {uuid: point}) — 归一化分数 + 点对象查找表
    """
    if not scored_hits:
        return {}, {}
    scores = [s for s, _ in scored_hits]
    min_s, max_s = min(scores), max(scores)
    denom = max_s - min_s + 1e-6
    norm = {}
    lookup = {}
    for s, pt in scored_hits:
        pid = str(pt.id)
        norm[pid] = (s - min_s) / denom
        lookup[pid] = pt
    return norm, lookup


def _vector_names(client: QdrantClient, collection_name: str) -> tuple[set[str], set[str]]:
    """读取Collection命名Dense/Sparse空间；旧Collection返回空集合。"""

    info = client.get_collection(collection_name)
    params = info.config.params
    dense_config = getattr(params, "vectors", None)
    sparse_config = getattr(params, "sparse_vectors", None)
    dense_names = set(dense_config) if isinstance(dense_config, dict) else set()
    sparse_names = set(sparse_config or {}) if isinstance(sparse_config, dict) else set()
    return dense_names, sparse_names


def _query_named_vector(
    client: QdrantClient,
    *,
    collection_name: str,
    vector_name: str,
    vector: list | qm.SparseVector,
    top_k: int,
) -> list:
    """兼容qdrant-client 1.9 search API与新版Query API。"""

    query_points = getattr(client, "query_points", None)
    if callable(query_points):
        response = query_points(
            collection_name=collection_name,
            query=vector,
            using=vector_name,
            limit=top_k,
            with_payload=True,
        )
        return list(response.points)
    if isinstance(vector, qm.SparseVector):
        query_vector = qm.NamedSparseVector(name=vector_name, vector=vector)
    else:
        query_vector = (vector_name, vector)
    return client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=top_k,
        with_payload=True,
    )


def _dense_search_scored(
    client: QdrantClient,
    dense_vec: list,
    top_k: int,
    collection_name: str,
    dense_names: set[str],
) -> list:
    """Dense 召回，保留 Qdrant 原始 score。返回 [(score, point), ...]"""
    if DENSE_VECTOR_NAME in dense_names:
        results = _query_named_vector(
            client,
            collection_name=collection_name,
            vector_name=DENSE_VECTOR_NAME,
            vector=dense_vec,
            top_k=top_k,
        )
    else:
        results = client.search(
            collection_name=collection_name,
            query_vector=dense_vec,
            limit=top_k,
        )
    return [(r.score, r) for r in results]


def _legacy_sparse_search_scored(
    client: QdrantClient,
    sparse_dict: dict,
    top_k: int,
    collection_name: str,
) -> list:
    """仅供旧Collection回退的Payload全量扫描。"""
    all_points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=collection_name, limit=200,
            offset=offset, with_payload=True, with_vectors=False)
        all_points.extend(batch)
        if offset is None:
            break

    scored = []
    for pt in all_points:
        p = pt.payload or {}
        indices = p.get("sparse_indices", [])
        values = p.get("sparse_values", [])
        s = sum(sparse_dict.get(int(idx), 0) * float(val) for idx, val in zip(indices, values))
        if s > 0:
            scored.append((s, pt))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


def _sparse_search_scored(
    client: QdrantClient,
    sparse_dict: dict,
    top_k: int,
    collection_name: str,
    sparse_names: set[str],
) -> list:
    """优先使用Qdrant原生BGE-M3 Sparse Index，旧库才允许Payload扫描。"""

    if BGE_SPARSE_VECTOR_NAME not in sparse_names:
        logger.warning("Collection %s 尚未迁移原生Sparse，使用Payload扫描回退", collection_name)
        return _legacy_sparse_search_scored(client, sparse_dict, top_k, collection_name)
    items = sorted((int(index), float(value)) for index, value in sparse_dict.items())
    if not items:
        return []
    vector = qm.SparseVector(
        indices=[item[0] for item in items],
        values=[item[1] for item in items],
    )
    results = _query_named_vector(
        client,
        collection_name=collection_name,
        vector_name=BGE_SPARSE_VECTOR_NAME,
        vector=vector,
        top_k=top_k,
    )
    return [(result.score, result) for result in results]


def _legacy_fulltext_search_scored(
    client: QdrantClient,
    query: str,
    top_k: int,
    collection_name: str,
) -> list:
    """Fulltext 召回，TF 匹配率作为 score。返回 [(score, point), ...]"""
    keywords = [w for w in query[:100].split() if len(w) >= 1]
    if not keywords:
        return []

    all_ids: set = set()
    for kw in keywords[:5]:
        try:
            batch, _ = client.scroll(
                collection_name=collection_name,
                scroll_filter=qm.Filter(must=[qm.FieldCondition(key="chunk_text", match=qm.MatchText(text=kw))]),
                limit=top_k, with_payload=True, with_vectors=False,
            )
            for p in batch:
                if p.payload and (p.payload.get("chunk_text") or "").strip():
                    all_ids.add(p.id)
        except Exception:
            continue

    if not all_ids:
        return []

    results = client.retrieve(collection_name=collection_name, ids=list(all_ids), with_payload=True)

    scored = []
    for pt in results:
        text = (pt.payload or {}).get("chunk_text", "")
        if not text.strip():
            continue
        hits = sum(1 for kw in keywords if kw in text)
        score = hits / len(keywords)
        if score > 0:
            scored.append((score, pt))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


def _bm25_search_scored(
    client: QdrantClient,
    query: str,
    language: str,
    top_k: int,
    collection_name: str,
    sparse_names: set[str],
) -> list:
    """使用离线构建的命名BM25 Sparse Vector获得真正可排序的词法分数。"""

    vector_name = BM25_ZH_VECTOR_NAME if language == "zh" else BM25_EN_VECTOR_NAME
    if vector_name not in sparse_names:
        return _legacy_fulltext_search_scored(client, query, top_k, collection_name)
    token_ids = sorted({sparse_token_id(token) for token in bm25_query_tokens(query, language)})
    if not token_ids:
        return []
    results = _query_named_vector(
        client,
        collection_name=collection_name,
        vector_name=vector_name,
        vector=qm.SparseVector(indices=token_ids, values=[1.0] * len(token_ids)),
        top_k=top_k,
    )
    return [(result.score, result) for result in results]


async def get_final_funnel_top3(
    query: str,
    dense_vec: list,
    sparse_dict: dict,
    qdrant_url: str = DEFAULT_QDRANT_URL,
    reranker_model: str = "BAAI/bge-reranker-v2-m3",
    reranker_api_url: str = "https://api.siliconflow.cn/v1/rerank",
    reranker_api_key: str = "",
    collection_name: str | None = None,
) -> list:
    """Cascade Funnel 主线 — 三层漏斗检索 (全异步)。

    Layer 1 - 召回层: Dense/Sparse/Fulltext 并发 Top-10 → ID 去重
    Layer 2 - 粗排层: 缺省惩罚动态归一化 + 语义保底防误杀 → Top-10
    Layer 3 - 精排层: 硅基流动 Reranker 交叉编码 → Top-3 (异常降级至粗排)

    由调用方提供已计算好的 dense_vec 和 sparse_dict，不重复 embed。
    """
    client = QdrantClient(url=qdrant_url)
    active_collection = collection_name or COLLECTION_NAME
    dense_names, sparse_names = await asyncio.to_thread(
        _vector_names, client, active_collection
    )
    query_analysis = calculate_query_specificity_details(query)

    # ═══════════════════════════════════════════════════════════
    # Layer 1: 召回层 — 三路并发 Top-10 + ID 物理去重
    # ═══════════════════════════════════════════════════════════
    dense_hits, sparse_hits, ft_hits = await asyncio.gather(
        asyncio.to_thread(
            _dense_search_scored,
            client,
            dense_vec,
            10,
            active_collection,
            dense_names,
        ),
        asyncio.to_thread(
            _sparse_search_scored,
            client,
            sparse_dict,
            10,
            active_collection,
            sparse_names,
        ),
        asyncio.to_thread(
            _bm25_search_scored,
            client,
            query,
            query_analysis.language,
            10,
            active_collection,
            sparse_names,
        ),
    )

    # ID 物理去重 — 保留首次出现的 point 对象
    seen: set = set()
    dedup_hits: list = []
    all_lookup: dict = {}
    for _score, pt in dense_hits + sparse_hits + ft_hits:
        pid = str(pt.id)
        if pid not in seen:
            seen.add(pid)
            dedup_hits.append(pt)
            all_lookup[pid] = pt

    logger.info("L1 召回完成，去重后共获得 %d 篇候选文档 (dense=%d sparse=%d ft=%d → dedup=%d)",
                len(dedup_hits), len(dense_hits), len(sparse_hits), len(ft_hits), len(dedup_hits))

    # ═══════════════════════════════════════════════════════════
    # Layer 2: 粗排层 — 动态归一化 + 语义保底
    # ═══════════════════════════════════════════════════════════
    norm_dense, _ = normalize(dense_hits)
    norm_sparse, _ = normalize(sparse_hits)
    norm_bm25, _ = normalize(ft_hits)

    S = query_analysis.specificity
    w_semantic = 1.0 - S
    w_literal = S

    final_scores: dict[str, float] = {}
    for pid in seen:
        nd = norm_dense.get(pid, 0.0)   # 缺省惩罚
        ns = norm_sparse.get(pid, 0.0)  # 缺省惩罚
        nb = norm_bm25.get(pid, 0.0)    # 缺省惩罚
        base_score = w_semantic * nd + w_literal * (0.5 * ns + 0.5 * nb)

        # 语义保底防误杀: 同义词场景 Dense 高分 → 保底 0.5
        if nd > 0.85:
            final_scores[pid] = max(base_score, 0.5)
        else:
            final_scores[pid] = base_score

    ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)[:10]
    coarse_top10 = [all_lookup[uid] for uid, _ in ranked if uid in all_lookup]

    logger.info(
        "L2 粗排完成，截断保留 Top-%d "
        "(lang=%s confidence=%.2f density=%.2f S=%.2f w_sem=%.2f w_lit=%.2f)",
        len(coarse_top10),
        query_analysis.language,
        query_analysis.confidence,
        query_analysis.signal_density,
        S,
        w_semantic,
        w_literal,
    )

    # ═══════════════════════════════════════════════════════════
    # Layer 3: 精排层 — 硅基流动 Reranker (异常降级至粗排)
    # ═══════════════════════════════════════════════════════════
    if not coarse_top10:
        return []

    documents = []
    for hit in coarse_top10:
        p = hit.payload if hasattr(hit, "payload") else {}
        documents.append((p.get("chunk_text") or "")[:1000])

    try:
        logger.info("开始调用 Reranker API...")
        async with httpx.AsyncClient(timeout=15.0) as async_client:
            resp = await async_client.post(
                reranker_api_url,
                json={"model": reranker_model, "query": query, "documents": documents},
                headers={"Authorization": f"Bearer {reranker_api_key}", "Content-Type": "application/json"},
            )
        resp.raise_for_status()
        data = resp.json()
        rerank_results = data.get("results", [])

        if not rerank_results:
            logger.warning("Reranker 返回空结果，触发降级至粗排 Top-3")
            return coarse_top10[:3]

        rerank_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        fine_top3 = []
        for item in rerank_results[:3]:
            idx = item["index"]
            if 0 <= idx < len(coarse_top10):
                fine_top3.append(coarse_top10[idx])

        logger.info("Funnel L3 Fine: %d candidates → top %d (Reranker)", len(coarse_top10), len(fine_top3))
        return fine_top3

    except Exception as e:
        logger.warning("Reranker 失败，触发降级: %s", e)
        return coarse_top10[:3]
