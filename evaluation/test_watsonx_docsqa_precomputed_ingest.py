"""Colab 预计算向量导入器的纯本地契约测试。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

import pytest

from evaluation.watsonx_docsqa_ingest import IngestError, POINT_NAMESPACE, SEPARATORS
from evaluation.watsonx_docsqa_precomputed_ingest import (
    MANIFEST_NAME,
    PrecomputedSignature,
    load_manifest,
    load_state,
    normalize_row,
    run_import,
    validate_archive,
)


def _row(index: int, *, vector_dim: int = 2) -> dict:
    doc_id = "IBM-DOC-1"
    chunk_id = f"{doc_id}/chunk_{index:04d}"
    return {
        "point_id": str(uuid.uuid5(POINT_NAMESPACE, chunk_id)),
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "title": "Title",
        "source": "https://example.test/doc",
        "text": f"Evidence {index}",
        "sha256": "a" * 64,
        "dense": [0.25] * vector_dim,
        "sparse": {"12": 0.5},
    }


def _write_zip(
    path: Path,
    rows: list[dict],
    *,
    vector_dim: int = 2,
    artifact_sha256: str | None = None,
    corpus_sha256: str = "b" * 64,
) -> dict:
    artifact_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=artifact_buffer, mode="wb", mtime=0) as target:
        for row in rows:
            target.write((json.dumps(row) + "\n").encode())
    artifact = artifact_buffer.getvalue()
    manifest = {
        "format_version": 1,
        "dataset": "watsonxDocsQA",
        "model": "BAAI/bge-m3",
        "precision": "fp16",
        "normalize_embeddings": True,
        "vector_dim": vector_dim,
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "separators": SEPARATORS,
        "documents": 1,
        "chunks": len(rows),
        "corpus_sha256": corpus_sha256,
        "artifact": "vectors.jsonl.gz",
        "artifact_sha256": artifact_sha256 or hashlib.sha256(artifact).hexdigest(),
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vectors.jsonl.gz", artifact)
        archive.writestr(MANIFEST_NAME, json.dumps(manifest))
    return manifest


def test_validate_archive_accepts_deterministic_points(tmp_path: Path) -> None:
    path = tmp_path / "vectors.zip"
    _write_zip(path, [_row(0), _row(1)])

    with zipfile.ZipFile(path) as archive:
        manifest = load_manifest(archive)
        summary = validate_archive(archive, manifest)

    assert summary.records == 2
    assert summary.unique_documents == 1
    assert summary.min_sparse_terms == 1
    assert summary.artifact_sha256 == manifest.artifact_sha256


def test_validate_archive_rejects_tampered_artifact_hash(tmp_path: Path) -> None:
    path = tmp_path / "vectors.zip"
    _write_zip(path, [_row(0)], artifact_sha256="0" * 64)

    with zipfile.ZipFile(path) as archive:
        loaded = load_manifest(archive)
        with pytest.raises(IngestError, match="SHA-256"):
            validate_archive(archive, loaded)


def test_normalize_row_rejects_point_id_not_derived_from_chunk() -> None:
    row = _row(0)
    row["point_id"] = str(uuid.uuid4())

    with pytest.raises(IngestError, match="Point ID"):
        normalize_row(row, row_number=1, vector_dim=2)


def test_normalize_row_rejects_wrong_dense_dimension() -> None:
    row = _row(0)
    row["dense"] = [0.25]

    with pytest.raises(IngestError, match="Dense 维度"):
        normalize_row(row, row_number=1, vector_dim=2)


def test_state_rejects_changed_artifact(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = PrecomputedSignature(
        collection="watsonx_docsqa_colab_v1",
        artifact_sha256="a" * 64,
        corpus_sha256="b" * 64,
        vector_dim=1024,
        chunks=6759,
    )
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "signature": asdict(original),
                "next_row": 128,
                "points_written": 128,
            }
        ),
        encoding="utf-8",
    )
    changed = PrecomputedSignature(
        collection="watsonx_docsqa_colab_v1",
        artifact_sha256="c" * 64,
        corpus_sha256="b" * 64,
        vector_dim=1024,
        chunks=6759,
    )

    with pytest.raises(IngestError, match="不匹配"):
        load_state(path, changed)


def test_validate_only_finishes_before_qdrant_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"doc_id":"IBM-DOC-1"}\n', encoding="utf-8")
    corpus_sha256 = hashlib.sha256(corpus.read_bytes()).hexdigest()
    path = tmp_path / "vectors.zip"
    _write_zip(path, [_row(0)], corpus_sha256=corpus_sha256)

    def fail_if_connected(*args: object, **kwargs: object) -> None:
        raise AssertionError("validate-only 不应连接 Qdrant")

    monkeypatch.setattr(
        "evaluation.watsonx_docsqa_precomputed_ingest.QdrantClient",
        fail_if_connected,
    )
    run_import(
        argparse.Namespace(
            zip=path,
            corpus=corpus,
            validate_only=True,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "validated"
    assert output["records"] == 1
