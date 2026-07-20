# 项目文档索引

> 更新时间：2026-07-20。本文用于区分“当前架构说明”“可执行 Runbook”和“历史经验”，
> 避免把早期 Infinity、HUST 专用 Prompt 或 Payload Sparse 实现误认为当前设计。

## 当前必读

| 文档 | 用途 | 当前状态 |
|---|---|---|
| [`../README.md`](../README.md) | 项目概览、已验证指标、启动方式 | 当前 |
| [`../PLAN.md`](../PLAN.md) | 已完成事项、v2门禁与下一步 | 当前 |
| [`self-developed-retrieval-algorithm.md`](self-developed-retrieval-algorithm.md) | Cascade Funnel原理、公式与边界 | 当前v2 |
| [`retrieval-v2-migration.md`](retrieval-v2-migration.md) | Qdrant升级、离线迁移、切换和回滚 | 已执行，可复用 |
| [`watsonx-docsqa-full-baseline.md`](watsonx-docsqa-full-baseline.md) | 30题生成、抽查和RAGAS工作流 | v1完成，v2待跑 |

## API与服务

| 文档 | 服务 |
|---|---|
| [`api/backend.md`](api/backend.md) | FastAPI、LangGraph与检索链 |
| [`api/embedding-service.md`](api/embedding-service.md) | BGE-M3 Dense/Sparse HTTP服务 |
| [`api/db-qdrant.md`](api/db-qdrant.md) | Qdrant 1.10、v1/v2 Collection与快照 |
| [`api/db-pg.md`](api/db-pg.md) | PostgreSQL、Checkpoint与指纹表 |
| [`api/frontend.md`](api/frontend.md) | 当前冻结的React/Nginx边界 |

## 历史经验

[`pitfall-guide.md`](pitfall-guide.md) 记录多阶段开发中遇到的容器、Infinity、认证和前端问题。
其中 Infinity、Redis、旧 `/api/chat/stream` 等章节只用于解释历史决策，不代表当前生产依赖。

## 事实来源优先级

文档与运行状态冲突时，按以下顺序核对：

1. 当前源码和 `docker-compose.yaml`；
2. 评测结果目录中的 `summary.json` / Manifest；
3. 根 README、PLAN 和本目录当前文档；
4. `pitfall-guide.md` 等历史记录。

任何新指标都必须同时记录 Collection、模型、问题数量、生成时间和覆盖率，不允许只更新
README中的单个均值而丢失运行签名。
