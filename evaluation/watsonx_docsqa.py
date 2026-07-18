#!/usr/bin/env python3
"""校验并标准化 IBM watsonxDocsQA 数据集。

该模块只负责离线数据准备，不调用 Embedding、Qdrant、Backend 或大模型。它把
Hugging Face Parquet 数据转换为项目内部稳定的 JSONL 契约，供后续批量入库和
固定问题集评测共同使用。

标准输出包含：

``corpus.jsonl``
    每行一份文档，字段为 ``doc_id/title/text/url``。

``train.jsonl`` 与 ``test.jsonl``
    每行一道题，字段为 ``question_id/split/question/reference_answer/``
    ``gold_doc_ids/reference_contexts``。

``manifest.json``
    记录源文件哈希、行数和完整性检查结果，确保服务器与本地使用的是同一版本。

脚本会拒绝导出缺少 Gold 文档、上下文无法在 Gold 文档中定位、ID 重复或必填
字段为空的数据，避免在耗时的向量化之后才发现数据不可评测。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


FORMAT_VERSION = 1
CORPUS_COLUMNS = {"doc_id", "url", "title", "document"}
QA_COLUMNS = {
    "question_id",
    "question",
    "correct_answer",
    "correct_answer_document_ids",
    "ground_truths_contexts",
}


class DatasetValidationError(ValueError):
    """源数据不满足固定评测所需契约。"""


@dataclass(frozen=True)
class DatasetPaths:
    """watsonxDocsQA 三个源 Parquet 文件的确定路径。"""

    corpus: Path
    train: Path
    test: Path


@dataclass(frozen=True)
class ValidationReport:
    """校验完成后的可序列化统计。"""

    corpus_documents: int
    train_questions: int
    test_questions: int
    unique_gold_documents: int
    duplicate_document_texts: int
    total_document_characters: int


def _import_pandas():
    """延迟导入离线依赖，缺失时给出可执行的安装提示。"""

    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "读取 Parquet 需要 evaluation 依赖；请在 evaluation 环境安装 "
            "pandas 与 pyarrow。"
        ) from error
    return pd


def discover_paths(source_dir: Path) -> DatasetPaths:
    """发现唯一的 corpus/train/test Parquet，拒绝模糊匹配。"""

    source_dir = source_dir.resolve()
    candidates = {
        "corpus": sorted((source_dir / "corpus").glob("train-*.parquet")),
        "train": sorted((source_dir / "question_answers").glob("train-*.parquet")),
        "test": sorted((source_dir / "question_answers").glob("test-*.parquet")),
    }
    invalid = {name: len(paths) for name, paths in candidates.items() if len(paths) != 1}
    if invalid:
        details = ", ".join(f"{name}={count}" for name, count in invalid.items())
        raise DatasetValidationError(f"源数据文件数量异常：{details}")
    return DatasetPaths(
        corpus=candidates["corpus"][0],
        train=candidates["train"][0],
        test=candidates["test"][0],
    )


def _require_columns(frame: Any, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise DatasetValidationError(f"{label} 缺少字段：{', '.join(missing)}")


def _required_text(value: Any, *, field: str, row_id: str) -> str:
    if value is None:
        raise DatasetValidationError(f"{row_id} 的 {field} 为空")
    text = str(value).strip()
    if not text:
        raise DatasetValidationError(f"{row_id} 的 {field} 为空")
    return text


def _as_string_list(value: Any, *, field: str, row_id: str) -> list[str]:
    """兼容当前单字符串字段及未来可能出现的 JSON/list 表示。"""

    if isinstance(value, (list, tuple, set)):
        items = list(value)
    elif value is None:
        items = []
    else:
        text = str(value).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            items = parsed if isinstance(parsed, list) else [parsed]
        else:
            items = [text]
    result = [str(item).strip() for item in items if str(item).strip()]
    if not result:
        raise DatasetValidationError(f"{row_id} 的 {field} 为空")
    return result


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_and_validate(source_dir: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], ValidationReport, DatasetPaths]:
    """加载、标准化并执行跨表完整性校验。"""

    pd = _import_pandas()
    paths = discover_paths(source_dir)
    corpus_frame = pd.read_parquet(paths.corpus)
    train_frame = pd.read_parquet(paths.train)
    test_frame = pd.read_parquet(paths.test)
    _require_columns(corpus_frame, CORPUS_COLUMNS, "corpus")
    _require_columns(train_frame, QA_COLUMNS, "train")
    _require_columns(test_frame, QA_COLUMNS, "test")

    corpus: list[dict[str, Any]] = []
    documents_by_id: dict[str, str] = {}
    for _, row in corpus_frame.iterrows():
        doc_id = _required_text(row["doc_id"], field="doc_id", row_id="corpus row")
        if doc_id in documents_by_id:
            raise DatasetValidationError(f"重复 doc_id：{doc_id}")
        text = _required_text(row["document"], field="document", row_id=doc_id)
        documents_by_id[doc_id] = text
        corpus.append(
            {
                "doc_id": doc_id,
                "title": _required_text(row["title"], field="title", row_id=doc_id),
                "text": text,
                "url": _required_text(row["url"], field="url", row_id=doc_id),
            }
        )

    questions: dict[str, list[dict[str, Any]]] = {"train": [], "test": []}
    all_question_ids: set[str] = set()
    all_gold_ids: set[str] = set()
    for split, frame in (("train", train_frame), ("test", test_frame)):
        for _, row in frame.iterrows():
            question_id = _required_text(
                row["question_id"], field="question_id", row_id=f"{split} row"
            )
            if question_id in all_question_ids:
                raise DatasetValidationError(f"重复 question_id：{question_id}")
            all_question_ids.add(question_id)
            gold_doc_ids = _as_string_list(
                row["correct_answer_document_ids"],
                field="correct_answer_document_ids",
                row_id=question_id,
            )
            missing_gold = [doc_id for doc_id in gold_doc_ids if doc_id not in documents_by_id]
            if missing_gold:
                raise DatasetValidationError(
                    f"{question_id} 的 Gold 文档不存在：{', '.join(missing_gold)}"
                )
            contexts = _as_string_list(
                row["ground_truths_contexts"],
                field="ground_truths_contexts",
                row_id=question_id,
            )
            normalized_gold_texts = [
                _normalize_whitespace(documents_by_id[doc_id]) for doc_id in gold_doc_ids
            ]
            for context in contexts:
                normalized_context = _normalize_whitespace(context)
                if not any(normalized_context in text for text in normalized_gold_texts):
                    raise DatasetValidationError(
                        f"{question_id} 的标准上下文无法在 Gold 文档中定位"
                    )
            all_gold_ids.update(gold_doc_ids)
            questions[split].append(
                {
                    "question_id": question_id,
                    "split": split,
                    "question": _required_text(
                        row["question"], field="question", row_id=question_id
                    ),
                    "reference_answer": _required_text(
                        row["correct_answer"], field="correct_answer", row_id=question_id
                    ),
                    "gold_doc_ids": gold_doc_ids,
                    "reference_contexts": contexts,
                }
            )

    text_counts: dict[str, int] = {}
    for item in corpus:
        normalized = _normalize_whitespace(item["text"])
        text_counts[normalized] = text_counts.get(normalized, 0) + 1
    duplicate_texts = sum(count - 1 for count in text_counts.values() if count > 1)
    report = ValidationReport(
        corpus_documents=len(corpus),
        train_questions=len(questions["train"]),
        test_questions=len(questions["test"]),
        unique_gold_documents=len(all_gold_ids),
        duplicate_document_texts=duplicate_texts,
        total_document_characters=sum(len(item["text"]) for item in corpus),
    )
    return corpus, questions, report, paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for record in records:
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            target.write("\n")


def export_dataset(source_dir: Path, output_dir: Path) -> ValidationReport:
    """校验后导出可移植 JSONL，并返回统计报告。"""

    corpus, questions, report, paths = load_and_validate(source_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "corpus.jsonl", corpus)
    _write_jsonl(output_dir / "train.jsonl", questions["train"])
    _write_jsonl(output_dir / "test.jsonl", questions["test"])
    manifest = {
        "format_version": FORMAT_VERSION,
        "dataset": "watsonxDocsQA",
        "validation": asdict(report),
        "source_sha256": {
            "corpus": _sha256(paths.corpus),
            "train": _sha256(paths.train),
            "test": _sha256(paths.test),
        },
        "files": {
            "corpus": "corpus.jsonl",
            "train": "train.jsonl",
            "test": "test.jsonl",
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验并标准化 watsonxDocsQA")
    parser.add_argument("--source", type=Path, required=True, help="下载后的数据集根目录")
    parser.add_argument("--output", type=Path, help="标准化输出目录")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只做完整性校验，不生成文件",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.validate_only and args.output is None:
        raise SystemExit("未指定 --output；如只需检查请使用 --validate-only")
    if args.validate_only:
        _, _, report, _ = load_and_validate(args.source)
    else:
        report = export_dataset(args.source, args.output)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
