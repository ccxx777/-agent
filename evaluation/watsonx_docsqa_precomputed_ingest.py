#!/usr/bin/env python3
"""校验并导入 Colab 预计算的 watsonxDocsQA BGE-M3 向量。

该脚本是 CPU 入库器 :mod:`watsonx_docsqa_ingest` 的离线 GPU 配套入口。
Colab 只负责生成 Dense/Sparse 向量；服务器仍负责数据完整性校验、Qdrant
collection 创建、payload 构造和断点状态管理。因此切换计算设备不会绕过原有
的稳定 Point ID、IBM ``doc_id`` 和评测隔离边界。

安全约束：

1. 写入 Qdrant 前先完整校验 ZIP、manifest、artifact SHA-256 和每一条记录；
2. 校验 ``corpus.jsonl`` SHA-256，禁止把其他语料的向量混入本次基准；
3. Point ID 必须等于项目 namespace 对 ``chunk_id`` 计算出的 UUIDv5；
4. 只创建指定的新 collection，不删除或重建任何已有 collection；
5. 每批成功后原子保存 ``next_row``，中断后可幂等续传。

脚本应在现有 ``backend`` 容器中执行。``evaluation/`` 与 ``data/`` 已由开发
compose 配置挂载，因此无需 rebuild backend，也不会再次调用 embedding_service。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from qdrant_client import QdrantClient

if __package__:
    from .watsonx_docsqa_ingest import (
        POINT_NAMESPACE,
        SEPARATORS,
        ChunkRecord,
        IngestError,
        build_points,
        ensure_collection,
        sha256_file,
        upsert_with_retry,
    )
else:  # 直接执行 /app/evaluation/*.py 时，脚本目录位于 sys.path[0]
    from watsonx_docsqa_ingest import (  # type: ignore[no-redef]
        POINT_NAMESPACE,
        SEPARATORS,
        ChunkRecord,
        IngestError,
        build_points,
        ensure_collection,
        sha256_file,
        upsert_with_retry,
    )


FORMAT_VERSION = 1
STATE_VERSION = 1
MANIFEST_NAME = "watsonx_docsqa_bge_m3_manifest.json"
REQUIRED_ROW_FIELDS = {
    "point_id",
    "doc_id",
    "chunk_id",
    "title",
    "source",
    "text",
    "sha256",
    "dense",
    "sparse",
}


@dataclass(frozen=True)
class ArtifactManifest:
    """经过类型归一化的 Colab artifact 契约。"""

    artifact: str
    artifact_sha256: str
    corpus_sha256: str
    documents: int
    chunks: int
    vector_dim: int
    chunk_size: int
    chunk_overlap: int
    model: str
    precision: str


@dataclass(frozen=True)
class ValidationSummary:
    """第一遍流式校验得到的、可打印且可测试的统计。"""

    records: int
    unique_documents: int
    min_sparse_terms: int
    max_sparse_terms: int
    artifact_sha256: str


@dataclass(frozen=True)
class PrecomputedSignature:
    """决定断点状态能否复用的不可变输入。"""

    collection: str
    artifact_sha256: str
    corpus_sha256: str
    vector_dim: int
    chunks: int


@dataclass
class PrecomputedState:
    """Qdrant 已确认写入到哪一行。"""

    version: int
    signature: dict[str, Any]
    next_row: int
    points_written: int


def _required_string(value: Any, *, field: str) -> str:
    """把 manifest/row 字段归一化为非空字符串。"""

    result = str(value or "").strip()
    if not result:
        raise IngestError(f"预计算数据字段 {field} 为空")
    return result


def load_manifest(archive: zipfile.ZipFile) -> ArtifactManifest:
    """读取并严格验证 ZIP 内 manifest 的静态配置。"""

    names = archive.namelist()
    if MANIFEST_NAME not in names:
        raise IngestError(f"ZIP 缺少 {MANIFEST_NAME}")
    try:
        body = json.loads(archive.read(MANIFEST_NAME))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IngestError("预计算 manifest 不是有效 JSON") from error

    if body.get("format_version") != FORMAT_VERSION:
        raise IngestError("预计算 manifest format_version 不兼容")
    if body.get("dataset") != "watsonxDocsQA":
        raise IngestError("预计算 manifest dataset 不是 watsonxDocsQA")
    if body.get("model") != "BAAI/bge-m3":
        raise IngestError("预计算 manifest model 不是 BAAI/bge-m3")
    if body.get("separators") != SEPARATORS:
        raise IngestError("预计算 manifest separators 与服务器不一致")
    if body.get("normalize_embeddings") is not True:
        raise IngestError("预计算 Dense 向量没有声明 normalize_embeddings=true")

    try:
        manifest = ArtifactManifest(
            artifact=_required_string(body.get("artifact"), field="artifact"),
            artifact_sha256=_required_string(
                body.get("artifact_sha256"), field="artifact_sha256"
            ),
            corpus_sha256=_required_string(
                body.get("corpus_sha256"), field="corpus_sha256"
            ),
            documents=int(body.get("documents")),
            chunks=int(body.get("chunks")),
            vector_dim=int(body.get("vector_dim")),
            chunk_size=int(body.get("chunk_size")),
            chunk_overlap=int(body.get("chunk_overlap")),
            model=str(body.get("model")),
            precision=_required_string(body.get("precision"), field="precision"),
        )
    except (TypeError, ValueError) as error:
        raise IngestError("预计算 manifest 数值字段无效") from error

    if Path(manifest.artifact).name != manifest.artifact:
        raise IngestError("manifest artifact 必须是 ZIP 根目录下的文件名")
    if manifest.artifact not in names:
        raise IngestError(f"ZIP 缺少 artifact：{manifest.artifact}")
    if len(manifest.artifact_sha256) != 64:
        raise IngestError("manifest artifact_sha256 长度不是 64")
    if manifest.documents <= 0 or manifest.chunks <= 0:
        raise IngestError("manifest documents/chunks 必须大于 0")
    if manifest.vector_dim <= 0:
        raise IngestError("manifest vector_dim 必须大于 0")
    if manifest.chunk_size <= 0:
        raise IngestError("manifest chunk_size 必须大于 0")
    if manifest.chunk_overlap < 0 or manifest.chunk_overlap >= manifest.chunk_size:
        raise IngestError("manifest chunk_overlap 无效")
    return manifest


def sha256_zip_member(archive: zipfile.ZipFile, name: str) -> str:
    """流式计算 ZIP member 的 SHA-256，不把 artifact 整体读入内存。"""

    digest = hashlib.sha256()
    with archive.open(name) as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_artifact_rows(
    archive: zipfile.ZipFile,
    manifest: ArtifactManifest,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """逐行解开 ZIP 内的 ``jsonl.gz``，并附带从 1 开始的行号。"""

    with archive.open(manifest.artifact) as compressed_member:
        with gzip.GzipFile(fileobj=compressed_member, mode="rb") as gzip_stream:
            with io.TextIOWrapper(gzip_stream, encoding="utf-8") as source:
                record_number = 0
                for physical_line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    record_number += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise IngestError(
                            f"预计算 artifact 物理行 {physical_line_number} 不是有效 JSON"
                        ) from error
                    if not isinstance(row, dict):
                        raise IngestError(
                            f"预计算 artifact 记录 {record_number} 不是 JSON object"
                        )
                    yield record_number, row


def normalize_row(
    row: dict[str, Any],
    *,
    row_number: int,
    vector_dim: int,
) -> tuple[ChunkRecord, list[float], dict[int, float]]:
    """验证单条预计算记录，并转换为现有 ``build_points`` 输入。"""

    missing = REQUIRED_ROW_FIELDS.difference(row)
    if missing:
        raise IngestError(
            f"预计算 artifact 第 {row_number} 行缺少字段：{sorted(missing)}"
        )

    point_id = _required_string(row.get("point_id"), field="point_id")
    doc_id = _required_string(row.get("doc_id"), field="doc_id")
    chunk_id = _required_string(row.get("chunk_id"), field="chunk_id")
    expected_point_id = str(uuid.uuid5(POINT_NAMESPACE, chunk_id))
    if point_id != expected_point_id:
        raise IngestError(
            f"预计算 artifact 第 {row_number} 行 Point ID 与 chunk_id 不匹配"
        )
    if not chunk_id.startswith(f"{doc_id}/chunk_"):
        raise IngestError(
            f"预计算 artifact 第 {row_number} 行 chunk_id 不属于 doc_id"
        )

    raw_dense = row.get("dense")
    if not isinstance(raw_dense, list) or len(raw_dense) != vector_dim:
        raise IngestError(
            f"预计算 artifact 第 {row_number} 行 Dense 维度不是 {vector_dim}"
        )
    try:
        dense = [float(value) for value in raw_dense]
    except (TypeError, ValueError) as error:
        raise IngestError(
            f"预计算 artifact 第 {row_number} 行 Dense 含非数值"
        ) from error
    if not all(math.isfinite(value) for value in dense):
        raise IngestError(f"预计算 artifact 第 {row_number} 行 Dense 含非有限值")

    raw_sparse = row.get("sparse")
    if not isinstance(raw_sparse, dict):
        raise IngestError(f"预计算 artifact 第 {row_number} 行 Sparse 不是 object")
    sparse: dict[int, float] = {}
    try:
        for raw_token_id, raw_weight in raw_sparse.items():
            token_id = int(raw_token_id)
            weight = float(raw_weight)
            if token_id < 0 or token_id >= 30000:
                raise ValueError("token id 超出项目过滤范围")
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError("weight 不是有限正数")
            sparse[token_id] = weight
    except (TypeError, ValueError) as error:
        raise IngestError(
            f"预计算 artifact 第 {row_number} 行 Sparse 含无效 token/weight"
        ) from error

    record = ChunkRecord(
        point_id=point_id,
        doc_id=doc_id,
        chunk_id=chunk_id,
        title=_required_string(row.get("title"), field="title"),
        source=_required_string(row.get("source"), field="source"),
        text=_required_string(row.get("text"), field="text"),
        sha256=_required_string(row.get("sha256"), field="sha256"),
    )
    return record, dense, sparse


def validate_archive(
    archive: zipfile.ZipFile,
    manifest: ArtifactManifest,
) -> ValidationSummary:
    """第一遍完整验证所有记录；该函数不会访问 Qdrant。"""

    artifact_sha256 = sha256_zip_member(archive, manifest.artifact)
    if artifact_sha256 != manifest.artifact_sha256:
        raise IngestError("预计算 artifact SHA-256 与 manifest 不一致")

    seen_point_ids: set[str] = set()
    seen_chunk_ids: set[str] = set()
    doc_ids: set[str] = set()
    sparse_counts: list[int] = []
    records = 0
    for row_number, row in iter_artifact_rows(archive, manifest):
        record, _, sparse = normalize_row(
            row,
            row_number=row_number,
            vector_dim=manifest.vector_dim,
        )
        if record.point_id in seen_point_ids:
            raise IngestError(f"预计算 artifact 存在重复 Point ID：{record.point_id}")
        if record.chunk_id in seen_chunk_ids:
            raise IngestError(f"预计算 artifact 存在重复 chunk_id：{record.chunk_id}")
        seen_point_ids.add(record.point_id)
        seen_chunk_ids.add(record.chunk_id)
        doc_ids.add(record.doc_id)
        sparse_counts.append(len(sparse))
        records += 1

    if records != manifest.chunks:
        raise IngestError(
            f"预计算 artifact 记录数为 {records}，manifest 声明 {manifest.chunks}"
        )
    if len(doc_ids) != manifest.documents:
        raise IngestError(
            f"预计算 artifact 文档数为 {len(doc_ids)}，manifest 声明 {manifest.documents}"
        )
    return ValidationSummary(
        records=records,
        unique_documents=len(doc_ids),
        min_sparse_terms=min(sparse_counts),
        max_sparse_terms=max(sparse_counts),
        artifact_sha256=artifact_sha256,
    )


def load_state(path: Path, signature: PrecomputedSignature) -> PrecomputedState:
    """读取断点；输入签名变化时拒绝跳过任何行。"""

    expected_signature = asdict(signature)
    if not path.exists():
        return PrecomputedState(
            version=STATE_VERSION,
            signature=expected_signature,
            next_row=0,
            points_written=0,
        )
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IngestError(f"预计算断点状态无法读取：{path}") from error
    if body.get("version") != STATE_VERSION:
        raise IngestError("预计算断点状态版本不兼容")
    if body.get("signature") != expected_signature:
        raise IngestError("预计算断点状态与本次 collection/artifact 不匹配")
    state = PrecomputedState(
        version=STATE_VERSION,
        signature=expected_signature,
        next_row=int(body.get("next_row") or 0),
        points_written=int(body.get("points_written") or 0),
    )
    if state.next_row < 0 or state.next_row > signature.chunks:
        raise IngestError("预计算断点 next_row 超出 artifact 范围")
    if state.points_written != state.next_row:
        raise IngestError("预计算断点 points_written 与 next_row 不一致")
    return state


def save_state(path: Path, state: PrecomputedState) -> None:
    """通过同目录临时文件替换，避免中断留下半个状态 JSON。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _batched_rows(
    rows: Iterable[tuple[int, dict[str, Any]]],
    batch_size: int,
) -> Iterator[list[tuple[int, dict[str, Any]]]]:
    """把流式行按 Qdrant upsert 大小分组。"""

    if batch_size <= 0:
        raise IngestError("upsert_batch_size 必须大于 0")
    batch: list[tuple[int, dict[str, Any]]] = []
    for item in rows:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def run_import(args: argparse.Namespace) -> None:
    """执行校验，按需创建 collection，并从断点批量写入。"""

    zip_path = args.zip.resolve()
    corpus_path = args.corpus.resolve()
    if not zip_path.is_file():
        raise IngestError(f"预计算 ZIP 不存在：{zip_path}")
    if not corpus_path.is_file():
        raise IngestError(f"corpus.jsonl 不存在：{corpus_path}")

    with zipfile.ZipFile(zip_path) as archive:
        manifest = load_manifest(archive)
        corpus_sha256 = sha256_file(corpus_path)
        if corpus_sha256 != manifest.corpus_sha256:
            raise IngestError("服务器 corpus.jsonl SHA-256 与 Colab manifest 不一致")
        validation = validate_archive(archive, manifest)
        print(
            json.dumps(
                {
                    "status": "validated",
                    **asdict(validation),
                    "corpus_sha256": corpus_sha256,
                    "vector_dim": manifest.vector_dim,
                    "model": manifest.model,
                    "precision": manifest.precision,
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        if args.validate_only:
            return

    state_path = (
        args.state or zip_path.parent / "precomputed-ingest-state.json"
    ).resolve()
    signature = PrecomputedSignature(
        collection=args.collection,
        artifact_sha256=manifest.artifact_sha256,
        corpus_sha256=manifest.corpus_sha256,
        vector_dim=manifest.vector_dim,
        chunks=manifest.chunks,
    )
    state = load_state(state_path, signature)
    state_exists = state_path.exists()

    qdrant = QdrantClient(url=args.qdrant_url, timeout=None)
    ensure_collection(
        qdrant,
        args.collection,
        manifest.vector_dim,
        state_exists=state_exists,
        state_has_progress=bool(state.next_row or state.points_written),
    )
    if not state_exists:
        save_state(state_path, state)

    if state.next_row == manifest.chunks:
        info = qdrant.get_collection(args.collection)
        collection_points = int(info.points_count or 0)
        if collection_points != manifest.chunks:
            raise IngestError(
                f"断点已完成，但 Qdrant collection points={collection_points}，"
                f"期望 {manifest.chunks}"
            )
        print(
            json.dumps(
                {
                    "status": "already_complete",
                    "collection": args.collection,
                    "points": state.points_written,
                    "collection_points": collection_points,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    started = time.monotonic()
    with zipfile.ZipFile(zip_path) as archive:
        pending_rows = (
            (row_number, row)
            for row_number, row in iter_artifact_rows(archive, manifest)
            if row_number > state.next_row
        )
        total_batches = math.ceil(
            (manifest.chunks - state.next_row) / args.upsert_batch_size
        )
        for batch_number, row_batch in enumerate(
            _batched_rows(pending_rows, args.upsert_batch_size), 1
        ):
            records: list[ChunkRecord] = []
            dense_vectors: list[list[float]] = []
            sparse_vectors: list[dict[int, float]] = []
            for row_number, row in row_batch:
                record, dense, sparse = normalize_row(
                    row,
                    row_number=row_number,
                    vector_dim=manifest.vector_dim,
                )
                records.append(record)
                dense_vectors.append(dense)
                sparse_vectors.append(sparse)
            points = build_points(
                records,
                dense_vectors,
                sparse_vectors,
                dataset_name="watsonxDocsQA",
            )
            upsert_with_retry(
                qdrant,
                args.collection,
                points,
                args.upsert_batch_size,
            )
            state.next_row += len(points)
            state.points_written += len(points)
            save_state(state_path, state)
            print(
                f"[{batch_number}/{total_batches}] "
                f"points={state.points_written}/{manifest.chunks} "
                f"elapsed={time.monotonic() - started:.1f}s",
                flush=True,
            )

    info = qdrant.get_collection(args.collection)
    collection_points = int(info.points_count or 0)
    if collection_points != manifest.chunks:
        raise IngestError(
            f"Qdrant collection points={collection_points}，期望 {manifest.chunks}"
        )
    print(
        json.dumps(
            {
                "status": "complete",
                "collection": args.collection,
                "points": state.points_written,
                "collection_points": collection_points,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "state_file": str(state_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """定义服务器验证/导入参数；默认值均指向隔离评测环境。"""

    parser = argparse.ArgumentParser(description="导入 Colab 预计算 watsonxDocsQA 向量")
    parser.add_argument("--zip", type=Path, required=True, help="Colab 输出 ZIP")
    parser.add_argument("--corpus", type=Path, required=True, help="prepared/corpus.jsonl")
    parser.add_argument("--state", type=Path, help="断点状态；默认与 ZIP 同目录")
    parser.add_argument("--collection", default="watsonx_docsqa_colab_v1")
    parser.add_argument("--qdrant-url", default="http://db_qdrant:6333")
    parser.add_argument("--upsert-batch-size", type=int, default=128)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="完整校验 ZIP，但不访问 Qdrant",
    )
    return parser


def main() -> int:
    """CLI 入口。"""

    args = build_parser().parse_args()
    run_import(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
