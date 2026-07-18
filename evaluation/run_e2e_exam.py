#!/usr/bin/env python3
"""E2E QA 评测引擎。

直接调用冻结 Retriever，执行“检索 → Prompt → LLM → Judge”诊断流程。它用于
算法级实验，不替代走生产 HTTP 主链的 ``ragas_eval.py`` 和 ``rag_smoke.py``。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 确保 backend/ 在 sys.path 上
_backend = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_backend))


def load_exam(jsonl_path: str) -> list[dict]:
    """加载考卷 JSONL，每行: {"query": str, "expected_context": [doc_id], "reference_output": str}"""
    path = Path(jsonl_path)
    if not path.exists():
        print(f"Exam file not found: {jsonl_path}")
        return []

    questions = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            questions.append(json.loads(line))
    return questions


def build_prompt(query: str, retrieved_docs: list[dict]) -> str:
    """构建 LLM 提示词。"""
    context = ""
    for i, doc in enumerate(retrieved_docs, 1):
        context += f"\n[文档{i}] {doc.get('chunk_text', '')}\n"

    return f"""你是一个 AI 研究助手。请基于以下参考文档回答问题。

{context}

问题：{query}

要求：
1. 如果文档包含答案，请给出准确回答并注明 [doc_id]。
2. 如果文档不包含答案，请回答"文档中未找到相关信息"。
3. 回答简洁，不超过 200 字。"""


async def call_llm(prompt: str, model=None, base_url=None, api_key=None) -> str:
    """调用 LLM 生成回答。"""
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model or "deepseek-v4-flash[1m]",
        base_url=base_url or "https://api.deepseek.com",
        api_key=api_key or "",
        streaming=False,
        temperature=0.1,
    )
    resp = await llm.ainvoke(prompt)
    return resp.content if hasattr(resp, 'content') else str(resp)


async def judge_answer(query: str, reference: str, actual: str, model=None, base_url=None, api_key=None) -> dict:
    """LLM 裁判：对比实际回答与参考答案，给出 1-5 分语义评分。"""
    from langchain_openai import ChatOpenAI

    judge_prompt = f"""你是一个严格的评测裁判。请对比"实际回答"与"参考答案"，给出 1-5 分的语义相似度评分，并判断是否存在幻觉。

评分标准：
  5 - 完全一致，核心信息准确
  4 - 基本一致，有少量细节差异
  3 - 部分相关，但遗漏或增加了信息
  2 - 勉强相关，大部分内容不匹配
  1 - 完全无关或错误

问题：{query}

参考答案：{reference}

实际回答：{actual}

请用 JSON 格式回复：
{{"score": <1-5的整数>, "hallucination": <true/false>, "reason": "<一句话理由>"}}"""

    llm = ChatOpenAI(
        model=model or "deepseek-v4-flash[1m]",
        base_url=base_url or "https://api.deepseek.com",
        api_key=api_key or "",
        streaming=False,
        temperature=0.0,
    )
    resp = await llm.ainvoke(judge_prompt)
    content = resp.content if hasattr(resp, 'content') else str(resp)

    # 解析 JSON
    try:
        # 提取 JSON 部分
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(content[start:end])
    except json.JSONDecodeError:
        pass
    return {"score": 0, "hallucination": False, "reason": f"解析失败: {content[:100]}"}


def generate_markdown_report(results: list[dict], report_path: str) -> None:
    """生成 Markdown 评测报告。"""
    lines = []
    lines.append("# E2E QA 评测报告")
    lines.append("")
    lines.append(f"**评测题目数**: {len(results)}")
    avg_score = sum(r.get("judge_score", 0) for r in results) / max(len(results), 1)
    hallucination_count = sum(1 for r in results if r.get("hallucination", False))
    lines.append(f"**平均评分**: {avg_score:.2f} / 5.0")
    lines.append(f"**幻觉次数**: {hallucination_count} / {len(results)}")
    lines.append("")

    # 表格
    lines.append("| # | 问题 | 召回路径 | 实际回答 | 参考答案 | Judge 评分 | 幻觉 |")
    lines.append("|---|------|---------|---------|---------|-----------|-----|")
    for i, r in enumerate(results, 1):
        q = r.get("query", "")[:40]
        paths = ", ".join(r.get("recall_paths", []))
        actual = r.get("actual_output", "")[:60]
        ref = r.get("reference_output", "")[:60]
        score = r.get("judge_score", "?")
        h = "⚠️" if r.get("hallucination") else "✅"
        lines.append(f"| {i} | {q} | {paths} | {actual} | {ref} | {score} | {h} |")

    lines.append("")
    lines.append("## 详细结果")
    lines.append("")
    for i, r in enumerate(results, 1):
        lines.append(f"### 第 {i} 题")
        lines.append(f"**问题**: {r.get('query', '')}")
        lines.append(f"**召回路径**: {', '.join(r.get('recall_paths', []))}")
        lines.append(f"**参考答案**: {r.get('reference_output', '')}")
        lines.append(f"**实际回答**: {r.get('actual_output', '')}")
        lines.append(f"**Judge 评分**: {r.get('judge_score', '?')} / 5  |  幻觉: {r.get('hallucination', False)}")
        lines.append(f"**裁判理由**: {r.get('judge_reason', '')}")
        lines.append(f"**检索文档数**: {r.get('retrieved_count', 0)}")
        lines.append("")

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport saved: {report_path}")


async def main():
    import argparse
    import os

    p = argparse.ArgumentParser(description="E2E QA 评测")
    p.add_argument("--exam", default="data/eval/e2e_exam.jsonl")
    p.add_argument("--report", default="data/eval/report_v2.md")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    p.add_argument("--embed-url", default="http://127.0.0.1:8001/embed")
    p.add_argument("--model", default=os.getenv("MAIN_MODEL", "deepseek-v4-flash[1m]"))
    p.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"))
    p.add_argument("--api-key", default=os.getenv("ANTHROPIC_AUTH_TOKEN", ""))
    args = p.parse_args()

    questions = load_exam(args.exam)
    if not questions:
        print("No exam questions found. Generating from data/raw/...")
        questions = await auto_generate_exam(args)
        if not questions:
            print("Failed to generate exam.")
            return

    print(f"Loaded {len(questions)} exam questions")
    print(f"Model: {args.model}")
    print("2-core mode: serial execution, sleep(1) between LLM calls\n")

    # 直接使用冻结的 Retriever，不再依赖动态 Component Registry。
    from app.components.retriever.qdrant.v2_0_0.main import run as run_retriever

    results = []
    for i, q in enumerate(questions):
        query = q["query"]
        expected = q.get("expected_context", [])
        reference = q.get("reference_output", "")
        print(f"[{i+1}/{len(questions)}] {query[:60]}...")

        # Step 1: 检索
        ret_result = run_retriever({
            "query": query,
            "top_k": args.top_k,
            "qdrant_url": args.qdrant_url,
            "embedding_url": args.embed_url,
            "search_types": ["dense", "sparse", "fulltext"],
        })
        docs = ret_result.get("data", {}).get("docs", [])
        recall_paths = list(set(p for d in docs for p in d.get("scores_per_path", {}).keys()))
        print(f"  ↳ Retrieval: {len(docs)} docs from {recall_paths}")

        # Step 2: Prompt + LLM
        prompt = build_prompt(query, docs)
        try:
            actual_output = await call_llm(prompt, args.model, args.base_url, args.api_key)
        except Exception as e:
            actual_output = f"[LLM 调用失败: {e}]"

        # Step 3: Judge
        if reference:
            try:
                judge = await judge_answer(query, reference, actual_output,
                                           args.model, args.base_url, args.api_key)
            except Exception as e:
                judge = {"score": 0, "hallucination": False, "reason": str(e)}
        else:
            judge = {"score": 0, "hallucination": False, "reason": "无参考答案"}

        results.append({
            "query": query,
            "expected_context": expected,
            "reference_output": reference,
            "actual_output": actual_output,
            "retrieved_docs": docs,
            "retrieved_count": len(docs),
            "recall_paths": recall_paths,
            "judge_score": judge.get("score", 0),
            "hallucination": judge.get("hallucination", False),
            "judge_reason": judge.get("reason", ""),
        })

        print(f"  ↳ Score: {judge.get('score', '?')}/5 | Hallucination: {judge.get('hallucination', False)}")

        # 2 核 CPU 限流：每次 LLM 调用后 sleep(1)
        if i < len(questions) - 1:
            time.sleep(1)

    # 生成报告
    generate_markdown_report(results, args.report)

    # 汇总
    avg = sum(r["judge_score"] for r in results) / max(len(results), 1)
    h_count = sum(1 for r in results if r["hallucination"])
    print(f"\nSummary: avg_score={avg:.2f}/5, hallucinations={h_count}/{len(results)}")


async def auto_generate_exam(args) -> list[dict]:
    """自动从 data/raw/ 读取文档，LLM 逆向生成 10 个 QA 测试对。"""
    raw_dir = Path("data/raw")
    files = list(raw_dir.glob("*.md")) + list(raw_dir.glob("*.txt"))
    if not files:
        # 尝试 hust 子目录
        hust = raw_dir / "hust"
        if hust.exists():
            files = list(hust.glob("*.md"))
    if not files:
        print("No documents found in data/raw/")
        return []

    # 读第一个文档的前 3000 字
    doc_text = files[0].read_text(encoding="utf-8")[:3000]
    print(f"Using document: {files[0].name} ({len(doc_text)} chars)")

    gen_prompt = f"""你是一个考试命题专家。请仔细阅读以下文档，生成 10 个高质量的问答题测试对。

要求：
1. 每个问题必须高度依赖原文，不能通过常识回答
2. 答案必须引用原文中的具体信息
3. 问题覆盖不同类型：定义题、数据题、流程题、对比题
4. expected_context 填写预期能回答该问题的文档片段关键词

文档内容：
{doc_text}

请用 JSONL 格式回复（每行一个 JSON）：
{{"query": "...", "expected_context": ["关键词1", "关键词2"], "reference_output": "..."}}
{{"query": "...", "expected_context": ["关键词1"], "reference_output": "..."}}"""

    try:
        answer = await call_llm(gen_prompt, args.model, args.base_url, args.api_key)
        # 解析 JSONL
        questions = []
        for line in answer.strip().split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    questions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if questions:
            # 保存到文件
            exam_path = Path(args.exam)
            exam_path.parent.mkdir(parents=True, exist_ok=True)
            exam_path.write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in questions),
                                encoding="utf-8")
            print(f"Auto-generated {len(questions)} questions → {exam_path}")
        return questions
    except Exception as e:
        print(f"Auto-generate failed: {e}")
        return []


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
