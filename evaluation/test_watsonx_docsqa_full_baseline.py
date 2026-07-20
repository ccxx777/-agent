"""watsonxDocsQA完整评测编排器的离线契约测试。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from evaluation.watsonx_docsqa_full_baseline import (
    WorkflowError,
    generation_command,
    validate_generation_summary,
)


def test_generation_command_has_no_limit_and_uses_fixed_collection() -> None:
    args = argparse.Namespace(
        docker_command="docker",
        backend_container="backend",
        container_questions="/app/data/test.jsonl",
        container_generation_output="/app/data/generation_baseline_v1",
        collection="watsonx_docsqa_colab_v1",
        expected_points=6759,
        embedding_timeout=60.0,
        question_timeout=180.0,
    )
    command = generation_command(args)

    assert command[:4] == ["docker", "exec", "backend", "python"]
    assert "--limit" not in command
    assert command[command.index("--collection") + 1] == "watsonx_docsqa_colab_v1"


def test_generation_summary_requires_30_successes(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(
        json.dumps(
            {
                "total_questions": 30,
                "completed_questions": 29,
                "failed_questions": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowError, match="30/30"):
        validate_generation_summary(path, expected_questions=30)
