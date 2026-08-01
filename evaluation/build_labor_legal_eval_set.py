#!/usr/bin/env python3
"""从本地 A 级法条 artifact 生成待专家复核的法律检索题集草稿。

该脚本不调用 RAG，也不把合同或用户数据发送给模型。它只读取已经准备好的
``articles.jsonl``，以法条原文、条号和官方元数据生成可追溯的问题草稿。
生成结果必须经过人工/法律专家复核后，才能作为发布门禁或正式评测集。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from itertools import combinations
from pathlib import Path
from typing import Any


def _read_articles(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("citation_eligible") is True and record.get("article_no"):
            records.append(record)
    return records


def _direct_question(record: dict[str, Any], index: int) -> dict[str, Any]:
    title = str(record.get("title") or "法律文件")
    article_no = str(record.get("article_no") or "该条")
    return {
        "question_id": f"labor_a_direct_{index:03d}",
        "question": f"根据《{title}》{article_no}，该条文规定了什么？",
        "reference_answer": str(record.get("article_text") or "").strip(),
        "gold_doc_ids": [record.get("doc_id", "")],
        "gold_article_ids": [record.get("article_id", "")],
        "expected_citations": [record.get("citation_label", "")],
        "difficulty": "single_article",
        "source_level": "A",
        "review_status": "DRAFT_NEEDS_EXPERT_REVIEW",
        "official_url": record.get("official_url", ""),
    }


def _cross_question(left: dict[str, Any], right: dict[str, Any], index: int) -> dict[str, Any]:
    left_title = str(left.get("title") or "法律文件")
    right_title = str(right.get("title") or "法律文件")
    left_no = str(left.get("article_no") or "该条")
    right_no = str(right.get("article_no") or "该条")
    answer = (
        f"《{left_title}》{left_no}：{left.get('article_text', '')}\n"
        f"《{right_title}》{right_no}：{right.get('article_text', '')}"
    ).strip()
    return {
        "question_id": f"labor_a_cross_{index:03d}",
        "question": (
            f"劳动合同审查同时涉及《{left_title}》{left_no}和《{right_title}》{right_no}时，"
            "两条规定分别解决什么问题？"
        ),
        "reference_answer": answer,
        "gold_doc_ids": [left.get("doc_id", ""), right.get("doc_id", "")],
        "gold_article_ids": [left.get("article_id", ""), right.get("article_id", "")],
        "expected_citations": [
            left.get("citation_label", ""),
            right.get("citation_label", ""),
        ],
        "difficulty": "cross_article",
        "source_level": "A",
        "review_status": "DRAFT_NEEDS_EXPERT_REVIEW",
        "official_url": [left.get("official_url", ""), right.get("official_url", "")],
    }


def build_questions(records: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ordered = list(records)
    direct_count = max(1, min(limit - 3, 12)) if limit > 3 else limit
    questions = [_direct_question(record, index) for index, record in enumerate(ordered[:direct_count], 1)]
    cross_count = min(3, max(0, limit - len(questions)))
    for index, (left, right) in enumerate(combinations(ordered[: max(6, cross_count + 1)], 2), 1):
        if index > cross_count:
            break
        questions.append(_cross_question(left, right, index))
    return questions[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 A 级劳动法专家评测题集草稿")
    parser.add_argument(
        "--articles",
        type=Path,
        default=Path("data/legal/labor_contract/prepared/a_level/articles.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/datasets/labor_legal_expert_draft.jsonl"),
    )
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit 必须大于 0")
    records = _read_articles(args.articles)
    if not records:
        raise SystemExit("没有找到可引用的 A 级法条记录")
    questions = build_questions(records, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in questions),
        encoding="utf-8",
    )
    print(json.dumps({"status": "draft", "records": len(questions), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
