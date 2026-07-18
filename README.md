# AI Research Assistant

面向华中科技大学规章制度的 Agent + RAG 系统。当前优先目标是稳定运行
RAG 主链；React 前端和自研 Cascade Funnel 召回算法暂时冻结。

## 当前服务

```text
frontend              React + Nginx（冻结）
backend               FastAPI + LangGraph Agent
embedding_service     BGE-M3 Dense/Sparse Embedding
db_pg                 用户与 LangGraph Checkpoint
db_qdrant             rag_chunks 向量集合
sentinel              独立 data_worker Compose，负责文档入库
```

Redis、Component Registry、YAML Pipeline 和 Backend 内旧 Sentinel 已退出生产结构。
遗留源码暂存在 `.Trash/legacy-architecture/`，便于回滚核对。

## RAG 主链

```text
Question
  → BGE-M3 dense/sparse
  → Cascade Funnel（三路召回 → 粗排 → Reranker Top-3）
  → Retrieval Adapter（不改变结果顺序）
  → 生成上下文
  → LLM Answer
```

Adapter 同时保留两类信息：

- `contexts`：真正传给生成模型的文本，用于 Faithfulness 等生成测评。
- `documents`：`point_id/doc_id/chunk_id/source/rank` 等结构化召回信息，用于 Recall@K、MRR 和问题定位。

## 启动

主体服务：

```powershell
docker compose up -d
```

独立 Sentinel：

```powershell
docker compose -f data_worker/docker-compose.yml up -d --build
```

## 快速修改 Backend

开发时首次启用代码挂载：

```powershell
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d backend
```

普通 Python 代码修改后只需：

```powershell
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml restart backend
```

依赖发生变化时才重新构建 Backend：

```powershell
docker compose build backend
docker compose up -d --no-deps backend
```

不要因为 Backend 代码变化重建 `embedding_service`。Embedding 镜像只在 Torch、
Transformers 或 `_bge_m3.py` 变化时重建。

## 现有 PostgreSQL 升级

`docker-entrypoint-initdb.d` 不会对已有数据卷重新执行。先备份数据库，再手动按顺序执行：

```powershell
psql -f backend/sql/migrations/000-add-rag-documents-sha256.sql
psql -f backend/sql/migrations/001-drop-legacy-tables.sql
```

第一份迁移是幂等的；第二份会删除旧 `messages`、`token_usage` 表，确认历史数据不再需要后才执行。

## RAG 冒烟检查

部署完成后先运行不依赖 RAGAS 的检查：

```powershell
docker exec backend python /app/evaluation/rag_smoke.py --backend-url http://127.0.0.1:8000
```

成功条件：API 同时返回非空 `answer`、`contexts` 和按 Rank 排序的 `documents`。

## RAGAS

当前固定 `ragas==0.4.3` 与 `langchain-community==0.3.31`，使用新 Schema。后者用于规避 RAGAS 0.4.3 对新版 `langchain-community` 的兼容问题：

```text
user_input
response
retrieved_contexts
reference
```

先确认 RAG 冒烟测试稳定，再运行：

```powershell
uv run --with-requirements evaluation/requirements.txt python evaluation/ragas_eval.py --data data/raw/hust --limit 20
```

测评依赖与生产 Backend 依赖分离，避免为了 RAGAS 重建 Backend 镜像。

## 主要目录

```text
backend/app/agent/          State、Prompts、Tools、Nodes 与 Graph 拓扑
backend/app/schemas/        Auth、Chat、Retrieval 数据契约
backend/app/api/            Auth、Chat、Sessions、Eval HTTP API
backend/app/services/       Auth、Chat、Session、Retrieval 用例服务
backend/app/infrastructure/ PostgreSQL、Qdrant、模型与 Embedding 适配
backend/embedding_service/  BGE-M3 服务
data_worker/ingest/         Loader、Chunker、Embedder、Writer、Fingerprint
evaluation/                冒烟、E2E、RAGAS 评测脚本与独立依赖
data/raw/hust/              QA 与知识库数据
docs/                       设计和部署文档
```
