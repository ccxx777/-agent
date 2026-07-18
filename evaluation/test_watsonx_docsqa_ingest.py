"""watsonxDocsQA 批量入库器的纯本地契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.watsonx_docsqa_ingest import (
    CorpusDocument,
    IngestError,
    IngestSignature,
    build_points,
    build_splitter,
    chunk_documents,
    load_corpus,
    load_state,
)


def test_chunk_and_point_payload_preserve_original_doc_id() -> None:
    document = CorpusDocument(
        doc_id="IBM-DOC-1",
        title="Example",
        text="First paragraph.\n\nSecond paragraph with evidence.",
        url="https://example.test/doc",
    )
    splitter = build_splitter(chunk_size=30, chunk_overlap=5)

    first = chunk_documents([document], splitter)
    second = chunk_documents([document], splitter)

    assert [item.point_id for item in first] == [item.point_id for item in second]
    points = build_points(
        first,
        [[0.0, 1.0] for _ in first],
        [{"12": 0.5} for _ in first],
        dataset_name="watsonxDocsQA",
    )
    assert all(point.payload["doc_id"] == "IBM-DOC-1" for point in points)
    assert all(point.payload["dataset"] == "watsonxDocsQA" for point in points)
    assert points[0].payload["sparse_indices"] == [12]


def test_load_corpus_rejects_duplicate_doc_id(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    row = {"doc_id": "same", "title": "T", "text": "Body", "url": "https://x"}
    corpus.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(IngestError, match="重复 doc_id"):
        load_corpus(corpus)


def test_state_rejects_changed_chunk_configuration(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "signature": {
                    "collection": "watsonx_docsqa_v1",
                    "corpus_sha256": "abc",
                    "chunk_size": 1000,
                    "chunk_overlap": 200,
                    "vector_dim": 1024,
                },
                "completed_doc_ids": [],
                "points_written": 0,
            }
        ),
        encoding="utf-8",
    )
    changed = IngestSignature(
        collection="watsonx_docsqa_v1",
        corpus_sha256="abc",
        chunk_size=800,
        chunk_overlap=200,
        vector_dim=1024,
    )

    with pytest.raises(IngestError, match="参数不匹配"):
        load_state(state_path, changed)
