# AI 知识库助手 — 当前实施计划

> 状态：RAG 稳定性优先。前端和 Cascade Funnel 召回算法冻结。

## 一、架构边界

### 保留

- LangGraph Agent 与 PostgreSQL Checkpointer
- 自研 Cascade Funnel 三路召回、粗排和 Reranker
- BGE-M3 独立 Embedding 服务
- Qdrant `rag_chunks`
- 独立 `data_worker` Sentinel
- RAGAS、E2E 和轻量冒烟脚本

### 合并

- 召回输出统一收口到 `app/schemas/retrieval.py`：Agent 生成、Eval API 和测评读取同一份上下文。
- 文档入库统一由 `data_worker/ingest/service.py` 编排，`cli.py` 和 `watcher.py` 分别负责入口与持续监听。
- 用户认证请求/响应模型统一收口到 `app/schemas/auth.py`，不再引用不存在的旧 models 层。
- PostgreSQL 会话摘要与归档结构统一由 `01-tables.sql`、`02-memory.sql` 管理。

### 淘汰（已退出生产路径）

- Component Registry 和动态自动发现
- YAML Pipeline
- Backend 内旧 Sentinel
- Redis
- 重复的 `messages`、`token_usage` 初始化表

以上遗留文件暂存在 `.Trash/legacy-architecture/`。

## 二、当前数据流

```text
POST /api/chat 或 POST /api/eval/rag_query
  → LangGraph
  → search_hust_rules
  → BGE-M3
  → get_final_funnel_top3（算法冻结）
  → Retrieval Adapter
      ├─ context：生成 Prompt
      ├─ contexts：评测实际生成上下文
      └─ documents：召回元数据和最终 Rank
  → LLM 生成答案
  → PostgreSQL Checkpoint
```

## 三、实施顺序

1. 使用 `evaluation/rag_smoke.py` 稳定验证完整 RAG 主链。
2. 修复 Embedding/Transformers 的确定版本组合，不随 Backend 修改重建。
3. 记录每次请求的 answer、contexts、documents 和阶段耗时。
4. 建立不依赖 LLM Judge 的 Recall@K、MRR 和来源命中基线。
5. 在 RAG 稳定后运行 RAGAS 0.4.3。
6. 再评估是否接入 DeepEval 或 TruLens。

## 四、明确不做

- 暂不修改 React 前端。
- 暂不调整三路召回、权重、Top-K、粗排和 Reranker。
- 暂不为了清理结构升级 Python、Torch 或 Transformers。
- 暂不把测评框架安装进生产 Backend 镜像。

## 五、验收标准

- Backend 和 Embedding 服务可独立构建、重启。
- 普通 Backend 代码修改不触发 Embedding 重建。
- Sentinel 能正确通过 SHA256 去重并记录 PostgreSQL 元数据。
- Eval API 返回 `answer + contexts + documents`。
- 冒烟脚本连续执行无结构错误。
- RAGAS 输入使用 `user_input/response/retrieved_contexts/reference`。
