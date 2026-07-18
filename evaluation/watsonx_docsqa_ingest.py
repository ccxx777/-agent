#!/usr/bin/env python3
"""把标准化 watsonxDocsQA corpus 批量写入独立 Qdrant collection。

该脚本专门在现有 ``backend`` 容器中执行，因此直接复用镜像内已有的
``httpx``、``langchain-text-splitters`` 与 ``qdrant-client``，不需要重新构建
Docker 镜像。宿主机的 ``evaluation/`` 和 ``data/`` 已挂载到容器内。

与通用 Data Worker 相比，这条离线基准入库路径有四个约束：

1. ``doc_id`` 必须保留 IBM 原始值，后续才能计算 Hit@K 与 MRR；
2. Embedding HTTP 连接复用，并按较大批次提交；
3. Qdrant Point 使用确定性 UUID，失败重跑不会产生重复数据；
4. 每批文档成功后原子写入状态文件，支持中断后继续。

脚本不会删除或重建已有 collection。如果目标 collection 中已有 Point、但没有
与本次配置匹配的状态文件，会直接拒绝继续，避免混入其他数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models


STATE_VERSION = 1
POINT_NAMESPACE = uuid.UUID("a3ea09bc-e3df-4a68-a77f-dc1a54644cf9")
SEPARATORS = ["\n\n", "\n", "。", "；", " ", ""]


class IngestError(RuntimeError):
    """入库配置、远端响应或断点状态不一致。"""


@dataclass(frozen=True)
class CorpusDocument:
    doc_id: str
    title: str
    text: str
    url: str


@dataclass(frozen=True)
class ChunkRecord:
    point_id: str
    doc_id: str
    chunk_id: str
    title: str
    source: str
    text: str
    sha256: str


@dataclass(frozen=True)
class IngestSignature:
    collection: str
    corpus_sha256: str
    chunk_size: int
    chunk_overlap: int
    vector_dim: int


@dataclass
class IngestState:
    version: int
    signature: dict[str, Any]
    completed_doc_ids: list[str]
    points_written: int


def _required_text(value: Any, *, field: str, row_number: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise IngestError(f"corpus.jsonl 第 {row_number} 行的 {field} 为空")
    return text


def load_corpus(path: Path) -> list[CorpusDocument]:
    """读取标准 corpus JSONL，并拒绝 ID 重复或缺字段。"""

    documents: list[CorpusDocument] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as source:
        for row_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise IngestError(f"corpus.jsonl 第 {row_number} 行不是有效 JSON") from error
            doc_id = _required_text(item.get("doc_id"), field="doc_id", row_number=row_number)
            if doc_id in seen_ids:
                raise IngestError(f"corpus.jsonl 存在重复 doc_id：{doc_id}")
            seen_ids.add(doc_id)
            documents.append(
                CorpusDocument(
                    doc_id=doc_id,
                    title=_required_text(item.get("title"), field="title", row_number=row_number),
                    text=_required_text(item.get("text"), field="text", row_number=row_number),
                    url=_required_text(item.get("url"), field="url", row_number=row_number),
                )
            )
    if not documents:
        raise IngestError("corpus.jsonl 没有有效文档")
    return documents


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    if chunk_size <= 0:
        raise IngestError("chunk_size 必须大于 0")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise IngestError("chunk_overlap 必须大于等于 0 且小于 chunk_size")
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SEPARATORS,
        length_function=len,
        is_separator_regex=False,
    )


def chunk_documents(
    documents: Iterable[CorpusDocument],
    splitter: RecursiveCharacterTextSplitter,
) -> list[ChunkRecord]:
    """生成带稳定 ID 的 Chunk；同一输入与参数总是得到同一 Point ID。"""

    records: list[ChunkRecord] = []
    for document in documents:
        document_sha256 = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
        chunks = splitter.split_text(document.text)
        if not chunks:
            raise IngestError(f"文档 {document.doc_id} 分块结果为空")
        for index, text in enumerate(chunks):
            chunk_id = f"{document.doc_id}/chunk_{index:04d}"
            records.append(
                ChunkRecord(
                    point_id=str(uuid.uuid5(POINT_NAMESPACE, chunk_id)),
                    doc_id=document.doc_id,
                    chunk_id=chunk_id,
                    title=document.title,
                    source=document.url,
                    text=text,
                    sha256=document_sha256,
                )
            )
    return records


def batched(items: list[Any], batch_size: int) -> Iterable[list[Any]]:
    if batch_size <= 0:
        raise IngestError("batch_size 必须大于 0")
    for offset in range(0, len(items), batch_size):
        yield items[offset : offset + batch_size]


class EmbeddingClient:
    """带连接复用和有限重试的 BGE-M3 HTTP 客户端。"""

    def __init__(self, endpoint: str, batch_size: int, retries: int = 3) -> None:
        self._endpoint = endpoint
        self._batch_size = batch_size
        self._retries = retries
        self._client = httpx.Client(timeout=None)

    def close(self) -> None:
        self._client.close()

    def embed(self, records: list[ChunkRecord]) -> tuple[list[list[float]], list[dict]]:
        dense: list[list[float]] = []
        sparse: list[dict] = []
        for record_batch in batched(records, self._batch_size):
            payload = {
                "texts": [record.text for record in record_batch],
                "dense": True,
                "sparse": True,
            }
            last_error: Exception | None = None
            for attempt in range(1, self._retries + 1):
                try:
                    response = self._client.post(self._endpoint, json=payload)
                    response.raise_for_status()
                    body = response.json()
                    batch_dense = body.get("dense") or []
                    batch_sparse = body.get("sparse") or []
                    if len(batch_dense) != len(record_batch):
                        raise IngestError("Embedding dense 返回数量与请求数量不一致")
                    if len(batch_sparse) != len(record_batch):
                        raise IngestError("Embedding sparse 返回数量与请求数量不一致")
                    dense.extend(batch_dense)
                    sparse.extend(batch_sparse)
                    last_error = None
                    break
                except (httpx.HTTPError, ValueError, IngestError) as error:
                    last_error = error
                    if attempt < self._retries:
                        time.sleep(2 ** (attempt - 1))
            if last_error is not None:
                raise IngestError(f"Embedding 连续失败：{last_error}") from last_error
        return dense, sparse


def build_points(
    records: list[ChunkRecord],
    dense_vectors: list[list[float]],
    sparse_vectors: list[dict],
    *,
    dataset_name: str,
) -> list[qdrant_models.PointStruct]:
    """构造与现有 Retriever 完全兼容的 Qdrant payload。"""

    if len(records) != len(dense_vectors) or len(records) != len(sparse_vectors):
        raise IngestError("Chunk、Dense 与 Sparse 数量不一致")
    points: list[qdrant_models.PointStruct] = []
    for record, dense, sparse in zip(records, dense_vectors, sparse_vectors):
        sparse_items = sorted((int(key), float(value)) for key, value in sparse.items())
        payload: dict[str, Any] = {
            "doc_id": record.doc_id,
            "chunk_id": record.chunk_id,
            "chunk_text": record.text,
            "title": record.title,
            "source": record.source,
            "sha256": record.sha256,
            "user_id": "evaluation",
            "dataset": dataset_name,
        }
        if sparse_items:
            payload["sparse_indices"] = [item[0] for item in sparse_items]
            payload["sparse_values"] = [item[1] for item in sparse_items]
        points.append(
            qdrant_models.PointStruct(
                id=record.point_id,
                vector=dense,
                payload=payload,
            )
        )
    return points


def _collection_names(client: QdrantClient) -> set[str]:
    return {item.name for item in client.get_collections().collections}


def ensure_collection(
    client: QdrantClient,
    collection: str,
    vector_dim: int,
    *,
    state_exists: bool,
    state_has_progress: bool,
) -> bool:
    """创建新集合；已有非空集合只有在存在断点状态时才允许继续。"""

    if collection not in _collection_names(client):
        if state_has_progress:
            raise IngestError(
                f"状态文件记录了已完成文档，但集合 {collection} 不存在；"
                "请保留现状并人工确认，不会自动跳过数据"
            )
        client.create_collection(
            collection_name=collection,
            vectors_config=qdrant_models.VectorParams(
                size=vector_dim,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
        client.create_payload_index(
            collection_name=collection,
            field_name="chunk_text",
            field_schema=qdrant_models.PayloadSchemaType.TEXT,
            wait=True,
        )
        client.create_payload_index(
            collection_name=collection,
            field_name="doc_id",
            field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
        return True

    info = client.get_collection(collection)
    points_count = int(info.points_count or 0)
    vectors_config = info.config.params.vectors
    existing_size = getattr(vectors_config, "size", None)
    if existing_size is not None and int(existing_size) != vector_dim:
        raise IngestError(
            f"集合 {collection} 向量维度为 {existing_size}，期望 {vector_dim}"
        )
    if points_count > 0 and not state_exists:
        raise IngestError(
            f"集合 {collection} 已有 {points_count} 个 Point，但没有匹配的状态文件；"
            "为避免混入数据，已拒绝继续"
        )
    if points_count == 0 and state_has_progress:
        raise IngestError(
            f"状态文件记录了已完成文档，但集合 {collection} 当前为空；"
            "不会根据失效状态跳过数据"
        )
    return False


def load_state(path: Path, signature: IngestSignature) -> IngestState:
    expected_signature = asdict(signature)
    if not path.exists():
        return IngestState(
            version=STATE_VERSION,
            signature=expected_signature,
            completed_doc_ids=[],
            points_written=0,
        )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IngestError(f"状态文件无法读取：{path}") from error
    if body.get("version") != STATE_VERSION:
        raise IngestError("状态文件版本不兼容")
    if body.get("signature") != expected_signature:
        raise IngestError("状态文件与本次 collection/corpus/分块参数不匹配")
    return IngestState(
        version=STATE_VERSION,
        signature=expected_signature,
        completed_doc_ids=[str(item) for item in body.get("completed_doc_ids") or []],
        points_written=int(body.get("points_written") or 0),
    )


def save_state(path: Path, state: IngestState) -> None:
    """同目录临时文件替换，避免进程中断留下半个 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def upsert_with_retry(
    client: QdrantClient,
    collection: str,
    points: list[qdrant_models.PointStruct],
    batch_size: int,
    retries: int = 3,
) -> None:
    for point_batch in batched(points, batch_size):
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                client.upsert(collection_name=collection, points=point_batch, wait=True)
                last_error = None
                break
            except Exception as error:  # qdrant-client 异常类型跨版本变化
                last_error = error
                if attempt < retries:
                    time.sleep(2 ** (attempt - 1))
        if last_error is not None:
            raise IngestError(f"Qdrant Upsert 连续失败：{last_error}") from last_error


def dry_run(documents: list[CorpusDocument], splitter: RecursiveCharacterTextSplitter) -> None:
    counts = [len(splitter.split_text(document.text)) for document in documents]
    summary = {
        "documents": len(documents),
        "chunks": sum(counts),
        "min_chunks_per_document": min(counts),
        "max_chunks_per_document": max(counts),
        "average_chunks_per_document": round(sum(counts) / len(counts), 2),
        "embedding_batches_at_12": math.ceil(sum(counts) / 12),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def run_ingest(args: argparse.Namespace) -> None:
    corpus_path = args.corpus.resolve()
    state_path = (args.state or corpus_path.parent / "ingest-state.json").resolve()
    documents = load_corpus(corpus_path)
    splitter = build_splitter(args.chunk_size, args.chunk_overlap)
    if args.dry_run:
        dry_run(documents, splitter)
        return

    signature = IngestSignature(
        collection=args.collection,
        corpus_sha256=sha256_file(corpus_path),
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        vector_dim=args.vector_dim,
    )
    state = load_state(state_path, signature)
    completed = set(state.completed_doc_ids)
    pending = [document for document in documents if document.doc_id not in completed]

    qdrant = QdrantClient(url=args.qdrant_url, timeout=None)
    ensure_collection(
        qdrant,
        args.collection,
        args.vector_dim,
        state_exists=state_path.exists(),
        state_has_progress=bool(state.completed_doc_ids or state.points_written),
    )
    if not state_path.exists():
        save_state(state_path, state)

    if not pending:
        info = qdrant.get_collection(args.collection)
        print(
            json.dumps(
                {
                    "status": "already_complete",
                    "documents": len(completed),
                    "state_points": state.points_written,
                    "collection_points": int(info.points_count or 0),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    embedder = EmbeddingClient(args.embedding_url, args.embedding_batch_size)
    started = time.monotonic()
    try:
        total_groups = math.ceil(len(pending) / args.document_batch_size)
        for group_number, document_batch in enumerate(
            batched(pending, args.document_batch_size), 1
        ):
            records = chunk_documents(document_batch, splitter)
            dense, sparse = embedder.embed(records)
            if any(len(vector) != args.vector_dim for vector in dense):
                raise IngestError(f"Embedding 返回向量维度不是 {args.vector_dim}")
            points = build_points(records, dense, sparse, dataset_name="watsonxDocsQA")
            upsert_with_retry(qdrant, args.collection, points, args.upsert_batch_size)
            completed.update(document.doc_id for document in document_batch)
            state.completed_doc_ids = sorted(completed)
            state.points_written += len(points)
            save_state(state_path, state)
            elapsed = time.monotonic() - started
            print(
                f"[{group_number}/{total_groups}] documents={len(completed)}/{len(documents)} "
                f"batch_chunks={len(records)} points={state.points_written} "
                f"elapsed={elapsed:.1f}s",
                flush=True,
            )
    finally:
        embedder.close()

    info = qdrant.get_collection(args.collection)
    print(
        json.dumps(
            {
                "status": "complete",
                "collection": args.collection,
                "documents": len(completed),
                "state_points": state.points_written,
                "collection_points": int(info.points_count or 0),
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "state_file": str(state_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量入库 watsonxDocsQA")
    parser.add_argument("--corpus", type=Path, required=True, help="prepared/corpus.jsonl")
    parser.add_argument("--state", type=Path, help="断点状态文件；默认与 corpus 同目录")
    parser.add_argument("--collection", default="watsonx_docsqa_v1")
    parser.add_argument("--qdrant-url", default="http://db_qdrant:6333")
    parser.add_argument("--embedding-url", default="http://embedding_service:8001/embed")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--vector-dim", type=int, default=1024)
    parser.add_argument("--embedding-batch-size", type=int, default=12)
    parser.add_argument("--document-batch-size", type=int, default=20)
    parser.add_argument("--upsert-batch-size", type=int, default=128)
    parser.add_argument("--dry-run", action="store_true", help="只统计分块，不访问远端服务")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_ingest(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
