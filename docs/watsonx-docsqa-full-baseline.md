# watsonxDocsQA v2 完整基线

> 这是一份通用 RAG 工程基线，不是劳动合同法律正确率报告。评测语料来自 IBM watsonx 文档，法律产品必须另建 A/B 级法律与案例复核集。

## 1. 运行签名

- Collection：`watsonx_docsqa_colab_v2`
- 文档数：1,144
- 评测问题：30（test split）
- 向量点：6,759
- Generator：`deepseek-v4-flash`
- Evaluator：`deepseek-v4-flash`
- RAGAS：`0.4.3`，`metrics.collections` API
- 最终检索输出：生产 Cascade Funnel Top-3
- 完成人工重点抽查：2026-07-22

评测 Collection 与生产 `rag_chunks` 隔离。它用于验证检索/生成代码的可复现性，不应直接切成生产法律语料库。

## 2. 检索门禁

### v1/v2 对照

| 指标 | `watsonx_docsqa_colab_v1` | `watsonx_docsqa_colab_v2` | 变化 |
|---|---:|---:|---:|
| Gold Hit@1 | 83.33% | 83.33% | 0 |
| Gold Hit@3 | 90.00% | 90.00% | 0 |
| MRR@3 | 86.67% | 86.67% | 0 |
| Mean Recall@3 | 90.00% | 90.00% | 0 |
| 平均检索延迟 | 6.884 秒 | 1.038 秒 | -5.846 秒 |

v2 通过“Hit@3 不得下降”门禁，同时将平均检索延迟降低到约原来的 1/6.63。v2 使用 Qdrant 原生 Sparse Vector 和 BM25 命名向量，避免旧的 Payload Sparse 全库扫描路径。

### 回归样本

- `test_3`、`test_5`、`test_8`：v1 和 v2 均未命中。
- `test_8`：英文 lexical 召回样本，未命中不是 v2 迁移引入的退化；后续优化必须保留它作为回归题。
- 评测时同时保存 `zero_hit_question_ids`、每题 trace、延迟和错误 ID，不能只看平均值。

## 3. 端到端生成基线

- 完成率：30 / 30
- Gold Hit@1 / Hit@3：83.33% / 90.00%
- 拒答数量：3
- 平均总延迟：5.938 秒
- P95 总延迟：9.513 秒
- 生成链路：`RetrievalService → AgentNodes.generate_answer → ANSWER_PROMPT`

生成回答必须保留 `answer`、`contexts`、`documents`、引用和每题延迟。拒答只能在没有足够证据时发生；有证据时的错误拒答需要单独抽查，不能被平均指标掩盖。

## 4. RAGAS 完整结果

| 指标 | 均值 | 计分题数 | 覆盖率 |
|---|---:|---:|---:|
| Answer Correctness | 0.653255 | 30 / 30 | 100% |
| Faithfulness | 0.908333 | 30 / 30 | 100% |
| Context Relevance | 0.891667 | 30 / 30 | 100% |

### 如何解读

1. Faithfulness 较高，说明回答大部分能被给定上下文支持，但不能推出法律结论可靠。
2. Context Relevance 较高，说明 Top-3 上下文整体相关；仍需要逐题查看未命中和错误引用。
3. Answer Correctness 低于前两项，主要受回答是否完整、是否错误拒答和参考答案标注质量影响。
4. RAGAS 是 LLM Judge，必须和 Gold Hit@K、人工抽查、规则级测试一起使用。

## 5. 当前已知限制

- 30 道题规模适合回归，不足以代表通用知识库所有领域。
- watsonxDocsQA 的参考答案和 golden documents 可能存在标注粒度差异；低 Answer Correctness 需要回看原问题和证据，而不是直接修改检索算法。
- Reranker 使用外部服务，网络、模型版本和额度会影响延迟。
- 评测问题为英文，`test_8` 暴露了英文 lexical 召回仍需优化；这项优化必须在同一门禁下进行。

## 6. 复现顺序

```text
1. 启动 Backend、Qdrant、Embedding Service
2. 用固定 questions 和指定 Collection 跑 retrieval baseline
3. 对比 old/new summary，确认 Hit@3 门禁
4. 运行 30 题答案生成并保存 details.jsonl
5. 人工抽查拒答、低 Faithfulness、低 Correctness 和缺少引用样本
6. 运行 RAGAS，保存每题 score 与总 summary
7. 将 Collection、模型、版本、输入 SHA-256 和覆盖率写入报告
```

RAGAS 依赖只放在 `evaluation/requirements.txt`，不加入生产 Backend 镜像。运行失败时先检查 RAG 主链返回结构，再检查评测器兼容性。

## 7. 与劳动合同产品的关系

本基线只证明通用检索和答案生成链路已经具备稳定的工程接口。劳动合同产品还需要：

- A 级法律条文和司法解释的版本化资料；
- B 级官方案例及其适用边界；
- 合同条款结构化 Schema；
- 确定性风险规则、事实补充问题和人工复核；
- 不把用户合同写入公共语料、不让 LLM 单独决定风险等级。

上述内容见根目录 [`PLAN.md`](../PLAN.md) 和合同流程图 [`contract-review-workflow-status.png`](contract-review-workflow-status.png)。
