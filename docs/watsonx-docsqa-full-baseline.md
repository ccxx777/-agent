# watsonxDocsQA 30题完整评测工作流

本文说明如何按“答案生成 → 统计与重点抽查 → 人工确认 → RAGAS”的顺序运行
固定30题基线。工作流不会修改生产集合、召回算法或Backend镜像。

## 设计边界

- 生成阶段复用 `RetrievalService + AgentNodes.generate_answer + ANSWER_PROMPT`。
- Collection固定为 `watsonx_docsqa_colab_v1`，预期包含6759个Point。
- 答案生成和RAGAS均可断点续跑。
- RAGAS不能越过人工抽查门；答案、Context或报告变化后，旧确认自动失效。
- RAGAS环境继续与Backend隔离，固定使用 `evaluation/requirements.txt` 中的
  `ragas==0.4.3`。

## 第一、二步：生成30题并制作抽查报告

在仓库根目录执行：

```bash
uv run python evaluation/watsonx_docsqa_full_baseline.py prepare
```

编排器会在Backend容器中执行生产同构答案生成，不需要Docker rebuild。若进程中断，
再次执行相同命令即可；已经成功生成的问题会被跳过。

只有达到30/30成功后，才会生成：

```text
data/benchmarks/watsonxDocsQA/results/generation_baseline_v1/
├── details.jsonl
├── failures.jsonl
├── summary.json
└── review/
    ├── review_summary.json
    ├── spotcheck.json
    ├── spotcheck.md
    └── review_manifest.json
```

`review_summary.json` 包含：

- Gold Hit@1和Hit@3；
- 拒答、缺少引用和越界引用；
- Context数量和字面量转义换行；
- 答案长度；
- Retrieval、Generation和Total的Mean、P50、P95及Max。

`spotcheck.md` 会优先纳入Gold漏召回、拒答、引用异常、Context数量异常、延迟离群、
最长/最短答案，再用固定位置样本补足。高风险样本不会因为默认目标为10而被丢弃，
所以实际抽查数可能超过10。

查看材料：

```bash
cat data/benchmarks/watsonxDocsQA/results/generation_baseline_v1/review/review_summary.json
less data/benchmarks/watsonxDocsQA/results/generation_baseline_v1/review/spotcheck.md
```

## 人工确认

逐题确认答案正确性、证据支撑和引用关系后执行：

```bash
uv run python evaluation/watsonx_docsqa_full_baseline.py approve \
  --reviewer ccxx \
  --note "已检查重点样本，同意运行v1完整RAGAS"
```

这会生成 `review/approval.json`。确认记录同时绑定：

- 30题问题、答案、Context和文档的输入SHA256；
- 抽查Manifest的SHA256；
- 各抽查产物的SHA256。

生成结果或抽查材料发生变化后，`score` 会拒绝沿用旧确认。

## 第三步：运行完整30题RAGAS

```bash
uv run \
  --with-requirements evaluation/requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  python evaluation/watsonx_docsqa_full_baseline.py score
```

评分固定包含：

- `answer_correctness`
- `faithfulness`
- `context_relevance`

每题每指标完成后立即写入独立断点。中断后重新执行同一命令即可继续，已有有效分数
不会重复调用Judge。只有三个指标都达到30/30覆盖率，完整基线才会标记完成。

输出目录：

```text
data/benchmarks/watsonxDocsQA/results/ragas_baseline_v1/
├── manifest.json
├── summary.json
├── baseline_report.md
└── scores/
    ├── test_1.json
    ├── ...
    └── test_30.json
```

`baseline_report.md` 汇总运行签名、生成完成率、Gold命中、拒答、延迟、人工审查人、
RAGAS均值及覆盖率。指标均值必须始终与覆盖率和重点样本一起解读。

## 常见失败

### 生成未达到30/30

保留当前目录，再次执行 `prepare`。生成脚本会跳过已成功题目，并重试未完成题目。

### 人工确认失效

重新执行 `prepare` 生成最新抽查材料，人工阅读后再次执行 `approve`。不要手工修改
哈希或确认文件。

### RAGAS部分指标超时

直接重新执行 `score`。成功的题目和指标会跳过，只重试没有有效数值的断点。

### 不需要执行的操作

此工作流不需要执行以下操作：

- `docker-compose build backend`
- 重建Embedding镜像
- 重新导入watsonxDocsQA
- 修改 `rag_chunks` 生产Collection
