# AI Research Assistant

一个面向通用文档问答的 Agent + RAG 项目。系统使用 LangGraph 组织检索与生成流程，
以 BGE-M3、Qdrant 和外部 Reranker 实现三层混合检索，并使用固定问题集与 RAGAS
分别评估检索和答案质量。

> 当前状态（2026-07-20）：生产知识库仍使用 `rag_chunks`；watsonxDocsQA 已完成
> v1 全量基线，并完成 `watsonx_docsqa_colab_v2` 原生 Sparse/BM25 离线迁移。
> v2 的 3 题检索 Smoke 已通过，完整 30 题 v1/v2 门禁比较正在执行，结果确认前不切换生产库。

## 项目能力

- 通用知识库问答：Prompt 不再绑定华中科技大学，按用户问题语言回答。
- 三层检索：L1 多路召回、L2 动态融合粗排、L3 云端 Reranker 精排 Top-3。
- 双语 Query Specificity：按中英文表达特征动态调整语义权重与字面权重。
- 增量入库：独立 Data Worker 完成读取、切块、Embedding、写入和 SHA-256 幂等记录。
- 稳定评测契约：答案生成实际使用的 `contexts` 与结构化 `documents` 同时返回。
- 可恢复评测：生成、人工抽查和 RAGAS 独立运行，均支持断点续跑。

## 当前服务

```text
frontend              React + Nginx（当前冻结，不是迭代重点）
backend               FastAPI + LangGraph Agent
embedding_service     BGE-M3 Dense/Sparse Embedding
db_pg                 用户、文档指纹与 LangGraph Checkpoint
db_qdrant             生产与隔离评测 Collection
sentinel              独立 data_worker，负责文档增量入库
```

Redis、Component Registry、YAML Pipeline 和 Backend 内旧 Sentinel 已退出生产结构。
相关遗留源码保存在 `.Trash/legacy-architecture/`，不参与当前运行。

## 当前 RAG 主链

```text
Question
  → search_knowledge_base
  → BGE-M3 Dense + Sparse Query Embedding
  → Query language / specificity analysis
  → L1：Dense + BGE-M3 Sparse + BM25 并发召回
  → L2：分路归一化 + 动态权重 + Dense 语义保底
  → L3：Qwen3 Reranker Top-3
  → RetrievalPayload(contexts + documents)
  → 仅基于上下文生成带引用答案
```

`RetrievalService` 是 Agent 与召回算法之间的唯一业务入口。Collection 通过
`RAG_COLLECTION` 注入，因此新库可以先离线评测，再通过配置切换，不需要覆盖旧库。

## 已验证结果

### watsonxDocsQA v1

固定 30 题检索基线：

| 指标 | 结果 |
|---|---:|
| Hit@1 | 83.33% |
| Hit@3 | 93.33% |
| MRR@3 | 88.33% |
| 平均检索延迟 | 4.376 秒 |
| P50 / P95 | 3.821 / 9.270 秒 |

完整答案生成与 RAGAS 基线：

| 指标 | 结果 |
|---|---:|
| 生成完成率 | 30/30 |
| Gold Hit@1 / Hit@3 | 80.00% / 93.33% |
| Answer Correctness | 0.613954 |
| Faithfulness | 0.794416 |
| Context Relevance | 0.900000 |
| RAGAS 覆盖率 | 100% |

这些数字属于特定运行签名，不能与后续 v2 结果混写。v1 已知 Gold 未命中题为
`test_3`、`test_5`；同时存在错误拒答、幻觉和单一 Gold 标注冲突，需要结合人工抽查解释。

### Retrieval v2

- Qdrant Server：1.10.1；Python Client：1.10.1。
- `watsonx_docsqa_colab_v1` 的 6759 个 Point 已离线迁移到 `watsonx_docsqa_colab_v2`。
- v2 使用 Named Dense、原生 BGE-M3 Sparse、英文 BM25 和中文 BM25。
- Qdrant `multilingual` Full-text Index 探测成功，旧 Sparse Payload 未复制。
- 3 题 Smoke：3/3 完成、零错误、Hit@3 66.67%，已知未命中仍为 `test_3`；P50 0.964 秒。
- 完整 30 题门禁尚未归档，当前不能宣称 v2 已优于 v1。

## 启动与开发

服务器当前使用 `docker-compose` 命令：

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d
```

普通 Backend Python 代码修改后：

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml restart backend
```

依赖发生变化时才重建 Backend：

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d --build --no-deps backend
```

不要因为 Backend 代码变化重建 `embedding_service`。Embedding 镜像只在 Torch、
Transformers 或 BGE-M3 服务实现变化时重建。

独立 Data Worker：

```bash
docker-compose -f data_worker/docker-compose.yml up -d --build
```

## 基础检查

```bash
curl http://127.0.0.1:8000/health
docker exec backend python /app/evaluation/rag_smoke.py --backend-url http://127.0.0.1:8000
```

RAG Smoke 的成功条件是同时返回非空 `answer`、`contexts` 和按最终 Rank 排序的
`documents`。先保证主链稳定，再运行 RAGAS。

## 评测入口

- [完整 30 题生成与 RAGAS 工作流](docs/watsonx-docsqa-full-baseline.md)
- [Retrieval v2 迁移与切换](docs/retrieval-v2-migration.md)
- [自研 Cascade Funnel 详解](docs/self-developed-retrieval-algorithm.md)

评测依赖固定在 `evaluation/requirements.txt`，不安装进生产 Backend。当前主要指标为：

- 检索：Hit@1、Hit@3、MRR@3、Mean Recall@3、Mean/P50/P95 latency。
- 生成：Answer Correctness、Faithfulness、Context Relevance 与人工抽查结论。

## 主要目录

```text
backend/app/agent/          State、Prompts、Tools、Nodes 与 Graph
backend/app/schemas/        Auth、Chat、Retrieval 数据契约
backend/app/api/            Auth、Chat、Sessions、Eval HTTP API
backend/app/services/       业务服务与 Query Specificity
backend/app/infrastructure/ PostgreSQL、Qdrant、模型与 Embedding 适配
backend/embedding_service/  BGE-M3 HTTP 服务
data_worker/ingest/         Loader、Chunker、Embedder、Writer、Fingerprint
evaluation/                 数据准备、检索、生成、抽查、RAGAS 与门禁比较
data/benchmarks/            隔离评测数据和结果（大文件不进入 Git）
data/raw/hust/              早期领域数据，保留但不再定义系统身份
docs/                       架构、API、迁移与评测文档
```

## 当前下一步

1. 完成 v2 的 30 题检索基线并通过 Hit@3 不退化门禁。
2. 分析逐题排名变化与 Mean/P95 延迟，而不是只看均值。
3. 门禁通过后运行 v2 的 30 题生成、人工抽查和 RAGAS。
4. 只有 v2 检索与生成均通过后，才讨论生产 `rag_chunks_v2` 的离线重建与切换。
5. 生产切换前让独立 Data Worker 显式接收同一个 `RAG_COLLECTION`，避免读新库、写旧库。
