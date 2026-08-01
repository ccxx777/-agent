# 存储边界：JSON 文件与 PostgreSQL

![JSON 文件、PostgreSQL、私有合同文件与 Qdrant 的边界](storage-boundary.png)

## 先看结论

| 数据 | 当前落点 | 是否作为生产合同问答上下文 |
|---|---|---|
| 法律语料 `prepared/*.json`、`*.jsonl`、`manifest.json` | `data/legal/.../prepared/` 文件系统 | 否；它们是 Data Worker 的离线入库输入，入库后由 Qdrant 提供检索 |
| 检索、生成、RAGAS 结果 | `data/benchmarks/**/results/` JSON/JSONL | 否；只用于评测、复盘和发布门禁 |
| 断点与诊断状态 | `legal-ingest-state/*.json`、Smoke 输出 JSON | 否；用于恢复任务和诊断 |
| 原始合同 PDF/DOC/DOCX | Backend 私有文件目录 `original.*` | 不直接进入 Prompt，也不进入 Qdrant |
| 合同元数据、质量与隐私统计 | PostgreSQL `contract_review_tasks` | 间接；用于鉴权、恢复和流程状态 |
| 条款页脱敏正文 | PostgreSQL `contract_review_pages.redacted_text` | 是；ChatService 读取后组装上下文 |
| 提取事实与用户确认事实 | PostgreSQL `contract_review_tasks.extraction_result` / `confirmation_result` JSONB | 是；以结构化事实形式进入上下文 |
| 风险报告 | PostgreSQL `contract_review_reports.report` JSONB | 是；报告问答读取最新版本 |
| 会话消息、摘要、合同 scope | PostgreSQL LangGraph checkpoint 与 `sessions` | 是；用于连续对话和合同 A/B 隔离 |
| 法律库向量、全文索引和 payload | Qdrant 法律 Collection | 只作为法律检索结果，不保存私有合同 |

## 关键边界

1. JSON 文件是离线工件，不是在线合同数据的唯一真相源。删除合同或到期清理不会依赖某个 JSON 文件完成权限判断。
2. PostgreSQL 保存的是可鉴权、可恢复的生产数据；其中 `JSONB` 字段仍然是数据库字段，不等同于文件系统里的 JSON 文件。
3. `ChatService` 每次问答会从 PostgreSQL 读取脱敏正文、事实 JSON 和风险报告 JSON，临时组装 `contract_context`；这个组装结果不会另存为一个独立 JSON 文件。
4. 原始合同只在私有目录短期留存；原始二进制、未脱敏 OCR 图像和私有路径不会写入共享 Qdrant。
5. `data/legal/.../prepared/` 的法条 JSON/JSONL 经过 Data Worker 入库后，在线检索读取 Qdrant；评测 JSON 也不会自动变成生产知识库。

## 迁移提示

已有 PostgreSQL 部署需要执行 `backend/sql/migrations/007-session-conversation-scope.sql`，为旧 LangGraph checkpoint 建立一次性 scope 迁移状态。新建数据库会由 `backend/sql/init/01-tables.sql` 创建对应字段。
