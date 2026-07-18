"""watsonxDocsQA 离线适配器测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from evaluation.watsonx_docsqa import DatasetValidationError, export_dataset


def _write_source(root: Path, *, missing_gold: bool = False) -> None:
    (root / "corpus").mkdir(parents=True)
    (root / "question_answers").mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "doc_id": "doc-1",
                "url": "https://example.test/doc-1",
                "title": "Example",
                "document": "Alpha evidence appears in this document.",
                "md_document": "# Example\n\nAlpha evidence appears in this document.",
                "html_document": "<p>Alpha evidence appears in this document.</p>",
            }
        ]
    ).to_parquet(root / "corpus" / "train-00000-of-00001.parquet")
    question = {
        "question_id": "q-1",
        "question": "Where does the evidence appear?",
        "correct_answer": "In this document.",
        "correct_answer_document_ids": "missing" if missing_gold else "doc-1",
        "ground_truths_contexts": "Alpha evidence appears in this document.",
    }
    pd.DataFrame([question]).to_parquet(
        root / "question_answers" / "train-00000-of-00001.parquet"
    )
    pd.DataFrame([{**question, "question_id": "q-2"}]).to_parquet(
        root / "question_answers" / "test-00000-of-00001.parquet"
    )


def test_export_dataset_writes_stable_contract(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_source(source)

    report = export_dataset(source, output)

    assert report.corpus_documents == 1
    assert report.train_questions == 1
    assert report.test_questions == 1
    corpus = json.loads((output / "corpus.jsonl").read_text(encoding="utf-8"))
    train = json.loads((output / "train.jsonl").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert corpus == {
        "doc_id": "doc-1",
        "title": "Example",
        "text": "Alpha evidence appears in this document.",
        "url": "https://example.test/doc-1",
    }
    assert train["gold_doc_ids"] == ["doc-1"]
    assert train["reference_contexts"] == ["Alpha evidence appears in this document."]
    assert manifest["format_version"] == 1
    assert len(manifest["source_sha256"]["corpus"]) == 64


def test_export_rejects_missing_gold_document(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source(source, missing_gold=True)

    with pytest.raises(DatasetValidationError, match="Gold 文档不存在"):
        export_dataset(source, tmp_path / "output")
