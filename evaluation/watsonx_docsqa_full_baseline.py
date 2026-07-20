"""watsonxDocsQA 30题生成、人工审查与 RAGAS 的可续跑编排器。

三阶段必须按顺序执行：

``prepare``
    在 Backend 容器中复用生产链生成30题答案，然后在宿主机生成统计和抽查报告。
``approve``
    人工阅读 ``spotcheck.md`` 后，为当前输入与报告写入带哈希的确认记录。
``score``
    验证确认仍有效，再调用 RAGAS 0.4.3 完成30题三指标评分。

编排器不会重建镜像，不修改召回集合，并复用生成脚本与 RAGAS 脚本各自的断点。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

if __package__:
    from .watsonx_docsqa_generation_review import (
        ReviewError,
        approve_review,
        load_generation_rows,
        validate_approval,
        write_review_artifacts,
    )
else:
    from watsonx_docsqa_generation_review import (  # type: ignore[no-redef]
        ReviewError,
        approve_review,
        load_generation_rows,
        validate_approval,
        write_review_artifacts,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENERATIONS = (
    REPO_ROOT
    / "data/benchmarks/watsonxDocsQA/results/generation_baseline_v1/details.jsonl"
)
DEFAULT_GENERATION_DIR = DEFAULT_GENERATIONS.parent
DEFAULT_REVIEW_DIR = DEFAULT_GENERATION_DIR / "review"
DEFAULT_RAGAS_DIR = (
    REPO_ROOT / "data/benchmarks/watsonxDocsQA/results/ragas_baseline_v1"
)
DEFAULT_QUESTIONS_CONTAINER = (
    "/app/data/benchmarks/watsonxDocsQA/prepared/test.jsonl"
)
DEFAULT_GENERATION_DIR_CONTAINER = (
    "/app/data/benchmarks/watsonxDocsQA/results/generation_baseline_v1"
)


class WorkflowError(RuntimeError):
    """完整评测工作流的阶段或输出不满足质量门。"""


def generation_command(args: argparse.Namespace) -> list[str]:
    """构造无Shell插值的容器内30题生成命令，便于审计和测试。"""

    return [
        args.docker_command,
        "exec",
        args.backend_container,
        "python",
        "/app/evaluation/watsonx_docsqa_generation_baseline.py",
        "--questions",
        args.container_questions,
        "--output",
        args.container_generation_output,
        "--collection",
        args.collection,
        "--expected-points",
        str(args.expected_points),
        "--embedding-timeout",
        str(args.embedding_timeout),
        "--question-timeout",
        str(args.question_timeout),
    ]


def validate_generation_summary(path: Path, *, expected_questions: int) -> dict[str, Any]:
    if not path.is_file():
        raise WorkflowError(f"生成summary不存在：{path}")
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowError(f"生成summary无法读取：{path}") from error
    if int(summary.get("total_questions") or 0) != expected_questions:
        raise WorkflowError("生成summary的total_questions不是完整题集")
    if int(summary.get("completed_questions") or 0) != expected_questions:
        raise WorkflowError("生成未达到30/30，请修复失败题后重新执行prepare")
    if int(summary.get("failed_questions") or 0) != 0:
        raise WorkflowError("本轮生成仍有失败题，请重新执行prepare")
    return summary


def run_prepare(args: argparse.Namespace) -> dict[str, Any]:
    """完成或续跑生成阶段，并产生等待人工确认的抽查材料。"""

    command = generation_command(args)
    print("[stage 1/3] 运行30题生产同构答案生成", flush=True)
    try:
        subprocess.run(command, cwd=REPO_ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise WorkflowError(f"答案生成命令失败：{error}") from error

    generation_dir = args.generations.resolve().parent
    generation_summary = validate_generation_summary(
        generation_dir / "summary.json",
        expected_questions=args.expected_questions,
    )
    rows = load_generation_rows(
        args.generations.resolve(),
        expected_questions=args.expected_questions,
    )
    print("[stage 2/3] 生成统计和重点抽查报告", flush=True)
    review_manifest = write_review_artifacts(
        rows,
        args.review_output.resolve(),
        expected_questions=args.expected_questions,
        spotcheck_target=args.spotcheck_target,
    )
    result = {
        "status": "awaiting_human_review",
        "generation": generation_summary,
        "review_manifest": review_manifest,
        "spotcheck_markdown": str(args.review_output.resolve() / "spotcheck.md"),
        "next_step": (
            "阅读spotcheck.md后执行 approve；未确认前 score 会拒绝运行"
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def run_approve(args: argparse.Namespace) -> dict[str, Any]:
    """记录人工审查人和说明，不执行自动评分。"""

    approval = approve_review(
        args.review_output.resolve(),
        reviewer=args.reviewer,
        note=args.note,
    )
    print(json.dumps(approval, ensure_ascii=False, indent=2), flush=True)
    return approval


def _write_final_report(
    path: Path,
    *,
    generation_summary: dict[str, Any],
    review_summary: dict[str, Any],
    approval: dict[str, Any],
    ragas_summary: dict[str, Any],
) -> None:
    metrics = ragas_summary["metrics"]
    lines = [
        "# watsonxDocsQA v1完整基线",
        "",
        "## 运行签名",
        "",
        f"- Collection：`{generation_summary['collection']}`",
        f"- Generator：`{generation_summary['generator']['model']}`",
        f"- Evaluator：`{ragas_summary['evaluator']['model']}`",
        f"- RAGAS：`{ragas_summary['framework']['version']}`",
        f"- 人工审查：{approval['reviewer']}，{approval['approved_at']}",
        f"- 输入SHA256：`{approval['input_sha256']}`",
        "",
        "## 生成与检索",
        "",
        f"- 完成率：{generation_summary['completed_questions']}/{generation_summary['total_questions']}",
        f"- Gold Hit@1：{review_summary['retrieval']['hit_at_1']:.2%}",
        f"- Gold Hit@3：{review_summary['retrieval']['hit_at_3']:.2%}",
        f"- 拒答数量：{review_summary['answers']['refusal_count']}",
        f"- 平均总延迟：{review_summary['latency_seconds']['total']['mean']:.3f} 秒",
        f"- P95总延迟：{review_summary['latency_seconds']['total']['p95']:.3f} 秒",
        "",
        "## RAGAS",
        "",
        "| 指标 | 均值 | 覆盖率 |",
        "|---|---:|---:|",
    ]
    for metric_name in ("answer_correctness", "faithfulness", "context_relevance"):
        metric = metrics[metric_name]
        lines.append(
            f"| {metric_name} | {metric['mean']:.6f} | {metric['coverage']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## 已知重点样本",
            "",
            f"- Gold未命中：{review_summary['retrieval']['zero_hit_question_ids']}",
            f"- 缺少引用：{review_summary['citations']['missing_question_ids']}",
            f"- 无效引用：{review_summary['citations']['invalid_question_ids']}",
            "",
            "> 指标均值必须与覆盖率一起解读；人工抽查结论见generation_baseline_v1/review/spotcheck.md。",
            "",
        ]
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    temporary.replace(path)


async def run_score(args: argparse.Namespace) -> dict[str, Any]:
    """通过人工质量门后运行完整RAGAS，并生成综合基线报告。"""

    approval = validate_approval(
        args.generations.resolve(),
        args.review_output.resolve(),
        expected_questions=args.expected_questions,
    )
    try:
        if __package__:
            from .watsonx_docsqa_ragas import run_ragas
        else:
            from watsonx_docsqa_ragas import run_ragas
    except ImportError as error:
        raise WorkflowError(
            "score需要evaluation/requirements.txt；请使用uv run --with-requirements执行"
        ) from error

    print("[stage 3/3] 人工确认有效，运行30题RAGAS", flush=True)
    ragas_args = SimpleNamespace(
        generations=args.generations,
        output=args.ragas_output,
        env_file=args.env_file,
        evaluator_model=args.evaluator_model,
        evaluator_base_url=args.evaluator_base_url,
        api_key_env=args.api_key_env,
        embed_url=args.embed_url,
        embed_timeout=args.embed_timeout,
        metric_timeout=args.metric_timeout,
        attempts=args.attempts,
        limit=None,
        metrics=["answer_correctness", "faithfulness", "context_relevance"],
    )
    ragas_summary = await run_ragas(ragas_args)
    incomplete = {
        name: payload
        for name, payload in ragas_summary["metrics"].items()
        if payload.get("coverage") != 1.0
    }
    if incomplete:
        raise WorkflowError(
            "RAGAS未达到30/30覆盖率；保留断点后重新执行score："
            + ", ".join(incomplete)
        )
    generation_summary = validate_generation_summary(
        args.generations.resolve().parent / "summary.json",
        expected_questions=args.expected_questions,
    )
    review_summary = json.loads(
        (args.review_output.resolve() / "review_summary.json").read_text(encoding="utf-8")
    )
    final_report = args.ragas_output.resolve() / "baseline_report.md"
    _write_final_report(
        final_report,
        generation_summary=generation_summary,
        review_summary=review_summary,
        approval=approval,
        ragas_summary=ragas_summary,
    )
    result = {
        "status": "complete",
        "ragas_summary": ragas_summary,
        "baseline_report": str(final_report),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--generations", type=Path, default=DEFAULT_GENERATIONS)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--expected-questions", type=int, default=30)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="watsonxDocsQA 30题完整评测工作流")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="续跑30题生成并产出人工抽查报告")
    _add_shared_paths(prepare)
    prepare.add_argument("--spotcheck-target", type=int, default=10)
    prepare.add_argument("--docker-command", default="docker")
    prepare.add_argument("--backend-container", default="backend")
    prepare.add_argument("--container-questions", default=DEFAULT_QUESTIONS_CONTAINER)
    prepare.add_argument(
        "--container-generation-output",
        default=DEFAULT_GENERATION_DIR_CONTAINER,
    )
    prepare.add_argument("--collection", default="watsonx_docsqa_colab_v1")
    prepare.add_argument("--expected-points", type=int, default=6759)
    prepare.add_argument("--embedding-timeout", type=float, default=60.0)
    prepare.add_argument("--question-timeout", type=float, default=180.0)

    approve = subparsers.add_parser("approve", help="人工抽查后确认当前不可变输入")
    _add_shared_paths(approve)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", default="")

    score = subparsers.add_parser("score", help="确认有效后运行完整30题RAGAS")
    _add_shared_paths(score)
    score.add_argument("--ragas-output", type=Path, default=DEFAULT_RAGAS_DIR)
    score.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    score.add_argument("--evaluator-model", default="deepseek-v4-flash")
    score.add_argument("--evaluator-base-url", default="https://api.deepseek.com")
    score.add_argument("--api-key-env", default="ANTHROPIC_AUTH_TOKEN")
    score.add_argument("--embed-url", default="http://127.0.0.1:8001/embed")
    score.add_argument("--embed-timeout", type=float, default=120.0)
    score.add_argument("--metric-timeout", type=float, default=180.0)
    score.add_argument("--attempts", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.expected_questions <= 0:
        raise SystemExit("[FAIL] expected-questions必须大于0")
    try:
        if args.command == "prepare":
            run_prepare(args)
        elif args.command == "approve":
            run_approve(args)
        else:
            if args.attempts <= 0:
                raise WorkflowError("attempts必须大于0")
            asyncio.run(run_score(args))
    except (ReviewError, WorkflowError) as error:
        raise SystemExit(f"[FAIL] {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
