"""watsonxDocsQA 答案生成基线的离线测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from evaluation.watsonx_docsqa_generation_baseline import (
    BaselineError,
    build_generation_summary,
    generate_question,
    load_existing_generations,
)
from evaluation.watsonx_docsqa_retrieval_baseline import Question


def _question(question_id: str = "test_1") -> Question:
    return Question(
        question_id=question_id,
        question="What is enabled?",
        reference_answer="The setting is enabled.",
        gold_doc_ids=["doc-1"],
        reference_contexts=["The setting is enabled."],
    )


class _Payload:
    contexts = ["The setting is enabled."]
    documents = []

    def model_dump_json(self) -> str:
        return json.dumps({"context": "[1] The setting is enabled."})


@pytest.mark.asyncio
async def test_generate_question_uses_structured_tool_message() -> None:
    service = SimpleNamespace(retrieve=lambda _query: None)

    async def retrieve(_query):
        return _Payload()

    service.retrieve = retrieve

    class Nodes:
        async def generate_answer(self, state):
            assert state["messages"][0].content == "What is enabled?"
            assert json.loads(state["messages"][1].content)["context"].startswith("[1]")
            return {"messages": [AIMessage(content="It is enabled [1].")]}

    row = await generate_question(
        _question(),
        retrieval_service=service,
        agent_nodes=Nodes(),
    )

    assert row["answer"] == "It is enabled [1]."
    assert row["contexts"] == ["The setting is enabled."]
    assert row["latency_seconds"]["total"] >= 0


def test_existing_generation_rejects_model_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "details.jsonl"
    path.write_text(
        json.dumps(
            {
                "question_id": "test_1",
                "question": "What is enabled?",
                "gold_doc_ids": ["doc-1"],
                "collection": "benchmark",
                "generator": {"model": "old-model"},
                "answer": "answer",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BaselineError, match="模型不一致"):
        load_existing_generations(
            path,
            {"test_1": _question()},
            collection="benchmark",
            model="new-model",
        )


def test_generation_summary_keeps_scoring_separate() -> None:
    questions = [_question("test_1"), _question("test_2")]
    results = {
        "test_1": {
            "question_id": "test_1",
            "answer": "answer",
            "contexts": ["a", "b", "c"],
            "latency_seconds": {"retrieval": 4.0, "generation": 2.0, "total": 6.0},
        }
    }

    summary = build_generation_summary(
        questions,
        results,
        collection="benchmark",
        model="model",
        base_url="https://example.test",
        collection_points=6759,
        failed_question_ids=["test_2"],
    )

    assert summary["completed_questions"] == 1
    assert summary["failed_questions"] == 1
    assert summary["mean_contexts_per_answer"] == 3.0
    assert summary["mean_latency_seconds"]["total"] == 6.0
    assert "metrics" not in summary
