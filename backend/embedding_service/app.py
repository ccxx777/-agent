"""BGE-M3 Embedding HTTP 服务 — CPU 推理优化。

单进程加载模型（2.2GB 只占一份内存），asyncio.Semaphore 限制并发，
配合 OMP_NUM_THREADS 控制单次推理的 CPU 线程数。

设计要点：
  - 单 worker：避免 N 个进程各自加载 2.2GB 模型导致 OOM
  - Semaphore(2)：最多 2 个并发 embedding 请求，留出 CPU 给其他服务
  - torch.set_num_threads(4)：每次推理用 4 线程，峰值 2×4=8 核
  - Docker cpus=8.0：硬限制，超出后由 cgroup 节流
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("embedding")

# ── CPU 线程控制 ──
# 这些必须在 import torch 之后、模型加载之前设置
CPU_THREADS = int(os.getenv("EMBEDDING_CPU_THREADS", "4"))
MAX_CONCURRENT = int(os.getenv("EMBEDDING_MAX_CONCURRENT", "2"))

torch.set_num_threads(CPU_THREADS)
# OpenMP / MKL 也需要同步限制，防止底层 BLAS 自开线程
os.environ["OMP_NUM_THREADS"] = str(CPU_THREADS)
os.environ["MKL_NUM_THREADS"] = str(CPU_THREADS)

# ── 模型路径（容器内挂载，只读） ──
MODEL_PATH = os.getenv("BGE_M3_MODEL_PATH", "/app/models/bge-m3")

# ── 全局模型实例（单进程加载一次） ──
_model = None
_semaphore: asyncio.Semaphore | None = None


def _load_model():
    """同步加载 BGE-M3 模型到内存。"""
    from _bge_m3 import BGEM3Embedder

    logger.info("Loading bge-m3 from %s (threads=%d)...", MODEL_PATH, CPU_THREADS)
    model = BGEM3Embedder(MODEL_PATH, use_fp16=False, device="cpu")
    logger.info("bge-m3 loaded successfully")
    return model


# ── FastAPI ──

app = FastAPI(title="BGE-M3 Embedding Service")


class EmbedRequest(BaseModel):
    texts: list[str]
    dense: bool = True
    sparse: bool = True


class EmbedResponse(BaseModel):
    dense: list[list[float]]
    sparse: list[dict[int, float]]


@app.on_event("startup")
async def startup():
    global _model, _semaphore
    loop = asyncio.get_running_loop()
    _model = await loop.run_in_executor(None, _load_model)
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    logger.info("Embedding service ready (max_concurrent=%d)", MAX_CONCURRENT)


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest):
    """文本向量化接口。

    输入 texts 列表，返回 dense (1024-dim) + sparse (token weights)。
    通过 Semaphore 限制并发，超出时排队等待而非直接拒绝。
    """
    if _model is None or _semaphore is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    async with _semaphore:
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None,
            lambda: _model.encode(
                req.texts,
                batch_size=12,
                max_length=8192,
                return_dense=req.dense,
                return_sparse=req.sparse,
                return_colbert_vecs=False,
            ),
        )

    dense_vecs: list[list[float]] = []
    if req.dense and output.get("dense_vecs") is not None:
        dense_vecs = [v.tolist() if hasattr(v, "tolist") else list(v) for v in output["dense_vecs"]]

    sparse_vecs: list[dict[int, float]] = []
    if req.sparse and output.get("lexical_weights") is not None:
        for weights in output["lexical_weights"]:
            sparse_vecs.append({int(k): float(v) for k, v in weights.items() if int(k) < 30000})

    return EmbedResponse(dense=dense_vecs, sparse=sparse_vecs)


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": _model is not None}
