"""retriever.qdrant:v2_0_0 — 企业级串联漏斗架构 (Cascade Funnel)。

三层漏斗:
  1. 召回层 (Recall) — Dense + Sparse + Fulltext 并发 Top-10 → ID 去重
  2. 粗排层 (Coarse Rank) — 缺省惩罚动态归一化 + 语义保底防误杀 → Top-10
  3. 精排层 (Fine Rank) — 硅基流动 Reranker 交叉编码 → Top-3 (异常降级至粗排)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

import httpx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

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

STOPWORDS = {"的", "是", "在", "了", "如何", "怎么", "办理", "规定", "哪些", "什么", "关于", "办法", "申请", "研究生"}


def calculate_query_specificity(query: str) -> float:
    """计算查询专业度 S ∈ [0.2, 0.8]。

    停用词越多 → 通用口语 → S 偏低（依赖语义向量）
    停用词越少 → 专有名词多 → S 偏高（依赖字面匹配）
    """
    count = sum(1 for sw in STOPWORDS if sw in query)
    specificity = 0.8 - count * 0.1
    return max(0.2, min(0.8, specificity))


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


def _dense_search_scored(client: QdrantClient, dense_vec: list, top_k: int) -> list:
    """Dense 召回，保留 Qdrant 原始 score。返回 [(score, point), ...]"""
    results = client.search(collection_name=COLLECTION_NAME, query_vector=dense_vec, limit=top_k)
    return [(r.score, r) for r in results]


def _sparse_search_scored(client: QdrantClient, sparse_dict: dict, top_k: int) -> list:
    """Sparse 召回，保留暴力内积 score。返回 [(score, point), ...]"""
    all_points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION_NAME, limit=200,
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


def _fulltext_search_scored(client: QdrantClient, query: str, top_k: int) -> list:
    """Fulltext 召回，TF 匹配率作为 score。返回 [(score, point), ...]"""
    keywords = [w for w in query[:100].split() if len(w) >= 1]
    if not keywords:
        return []

    all_ids: set = set()
    for kw in keywords[:5]:
        try:
            batch, _ = client.scroll(
                collection_name=COLLECTION_NAME,
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

    results = client.retrieve(collection_name=COLLECTION_NAME, ids=list(all_ids), with_payload=True)

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


async def get_final_funnel_top3(
    query: str,
    dense_vec: list,
    sparse_dict: dict,
    qdrant_url: str = DEFAULT_QDRANT_URL,
    reranker_model: str = "BAAI/bge-reranker-v2-m3",
    reranker_api_url: str = "https://api.siliconflow.cn/v1/rerank",
    reranker_api_key: str = "",
) -> list:
    """Cascade Funnel 主线 — 三层漏斗检索 (全异步)。

    Layer 1 - 召回层: Dense/Sparse/Fulltext 并发 Top-10 → ID 去重
    Layer 2 - 粗排层: 缺省惩罚动态归一化 + 语义保底防误杀 → Top-10
    Layer 3 - 精排层: 硅基流动 Reranker 交叉编码 → Top-3 (异常降级至粗排)

    由调用方提供已计算好的 dense_vec 和 sparse_dict，不重复 embed。
    """
    client = QdrantClient(url=qdrant_url)

    # ═══════════════════════════════════════════════════════════
    # Layer 1: 召回层 — 三路并发 Top-10 + ID 物理去重
    # ═══════════════════════════════════════════════════════════
    dense_hits, sparse_hits, ft_hits = await asyncio.gather(
        asyncio.to_thread(_dense_search_scored, client, dense_vec, 10),
        asyncio.to_thread(_sparse_search_scored, client, sparse_dict, 10),
        asyncio.to_thread(_fulltext_search_scored, client, query, 10),
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

    S = calculate_query_specificity(query)
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

    logger.info("L2 粗排完成，截断保留 Top-%d (S=%.2f w_sem=%.2f w_lit=%.2f)",
                len(coarse_top10), S, w_semantic, w_literal)

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
