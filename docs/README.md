# 项目文档索引

> 更新时间：2026-07-28
>
> 本目录区分当前实现、可执行 Runbook、评测记录和历史经验。文档中的“已完成”必须能在当前源码、Compose 配置或评测 `summary.json` 中找到证据。

## 推荐阅读顺序

| 文档 | 用途 | 状态 |
|---|---|---|
| [`../README.md`](../README.md) | 项目定位、当前能力、启动方式和安全边界 | 当前总览 |
| [`../PLAN.md`](../PLAN.md) | 开发阶段、完成项、下一步和验收门禁 | 当前计划 |
| [`contract-review-workflow-status.png`](contract-review-workflow-status.png) | 已完成能力与待开发 Workflow 的总览图 | 当前状态 |
| [`contract-upload-module.png`](contract-upload-module.png) | PDF/DOC/DOCX 上传、解析、脱敏和质量门禁 | 已实现基础模块 |
| [`api/backend.md`](api/backend.md) | Backend、RAG 和合同上传 API | 当前 API |
| [`self-developed-retrieval-algorithm.md`](self-developed-retrieval-algorithm.md) | L1/L2/L3 Cascade Funnel 的设计和边界 | 当前 v2 |
| [`retrieval-v2-migration.md`](retrieval-v2-migration.md) | Qdrant 升级、离线迁移、门禁和回滚 | 已执行，可复用 |
| [`watsonx-docsqa-full-baseline.md`](watsonx-docsqa-full-baseline.md) | 30 题检索、生成、人工抽查和 RAGAS 结果 | v2 完整基线 |
| [`pitfall-guide.md`](pitfall-guide.md) | Docker、依赖、评测和历史迁移中的常见问题 | 历史经验 |

## API 与基础设施

- [`api/backend.md`](api/backend.md)：FastAPI、LangGraph、合同上传和状态查询。
- [`api/embedding-service.md`](api/embedding-service.md)：BGE-M3 Dense/Sparse HTTP 接口。
- [`api/db-qdrant.md`](api/db-qdrant.md)：生产/评测 Collection、原生 Sparse 和快照。
- [`api/db-pg.md`](api/db-pg.md)：PostgreSQL、Checkpoint、指纹和合同任务表。
- [`api/frontend.md`](api/frontend.md)：当前 React/Nginx 前端的冻结边界。

## 合同审查资料

![合同审查 Workflow 当前状态](contract-review-workflow-status.png)

当前已落地的是合同上传、文件格式解析、私有存储、页级质量判断、隐私脱敏、条款切分、结构化事实提取和证据定位。A 级法律检索、B 级案例补充、规则风险分级和最终报告仍按 [`../PLAN.md`](../PLAN.md) 的顺序开发。

合同原文不进入公共 `rag_chunks` 或 `watsonx_docsqa_colab_v2`；扫描件 OCR 默认关闭，外部 OCR 原图发送必须通过显式配置并写入隐私审计字段。

## 评测资料

评测脚本位于 `../evaluation/`，通过真实 Backend/RetrievalService 运行，不安装进生产 Backend 镜像。最新 v2 关键结果：

- Retrieval：Hit@1 `83.33%`、Hit@3 `90.00%`、MRR@3 `86.67%`、平均检索 `1.038s`。
- Generation：30/30 完成，平均总延迟 `5.938s`，P95 `9.513s`。
- RAGAS 0.4.3：Answer Correctness `0.653255`、Faithfulness `0.908333`、Context Relevance `0.891667`，覆盖率 100%。

完整运行签名、零命中样本和指标解释见 [`watsonx-docsqa-full-baseline.md`](watsonx-docsqa-full-baseline.md)。

## 事实来源优先级

文档与运行状态冲突时，按以下顺序核对：

1. 当前源码和 `docker-compose.yaml` / `docker-compose.dev.yaml`；
2. 评测结果目录中的 `summary.json`、Manifest 和运行签名；
3. 根目录 `README.md`、`PLAN.md` 和本目录当前文档；
4. `pitfall-guide.md` 等历史记录。

`data/`、`.env`、模型权重、数据库持久化目录、真实合同样本和 `.codegraph/` 均属于本地边界，不应提交到 GitHub。
