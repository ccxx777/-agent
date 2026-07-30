# PostgreSQL + pgvector

| 容器名 | 端口 | 镜像 | 数据库 |
|--------|------|------|--------|
| `db_pg` | `5432` | `pgvector/pgvector:pg17` | `ai_assistant` |

## 连接

```
# 容器内
postgresql://admin:<password>@db_pg:5432/ai_assistant

# 宿主机
postgresql://admin:<password>@127.0.0.1:5432/ai_assistant
```

## 健康检查

```bash
# 容器内
pg_isready -U admin -d ai_assistant

# 宿主机
psql -h 127.0.0.1 -U admin -d ai_assistant -c "SELECT 1"
```

## 表结构

首次启动 `docker-entrypoint-initdb.d/01-tables.sql` 自动执行：

| 表 | 用途 | 关键列 |
|----|------|--------|
| `user_profiles` | 用户画像 | `user_id UUID PK`, `username UNIQUE`, `password_hash`, `name`, `age`, `gender`, `hometown`, `tags JSONB` |
| `sessions` | 会话记录与摘要 | `session_id UUID PK`, `user_id FK`, `title`, `summary` |
| `chat_messages` | 可选的对话归档 | `id BIGSERIAL PK`, `session_id FK`, `role`, `content`, `metadata JSONB` |
| `rag_documents` | 文档元数据与摄取去重 | `doc_id UUID PK`, `title`, `source`, `sha256`, `tags[]`, `chunk_count`, `indexed_at` |

LangGraph PostgresSaver 额外创建：

| 表 | 用途 |
|----|------|
| `checkpoints` | 图状态快照 (thread_id, checkpoint_id, state) |
| `checkpoint_blobs` | 序列化数据 |
| `checkpoint_writes` | 写操作日志 |

## 指纹去重

`rag_documents.sha256` 存储文件内容指纹，`source` 始终保存原始路径。Sentinel 入库前先查指纹：
- 已存在 + 路径相同 → SKIP
- 已存在 + 路径不同 → 同步 PG `source` 与 Qdrant `payload.source`
- 不存在 → 正常入库

已有部署先手动执行幂等迁移 `backend/sql/migrations/000-add-rag-documents-sha256.sql`。

`messages`、`token_usage` 属于旧初始化结构，新环境不再创建；已部署数据库如需移除，应先备份，再手动执行 `backend/sql/migrations/001-drop-legacy-tables.sql`。`migrations/` 不会被 PostgreSQL 初始化入口自动执行。

## 合同审查表与事实确认

`backend/sql/init/03-contract-review.sql` 会创建 `contract_review_tasks`、`contract_review_pages` 和 `contract_review_fact_confirmations`。已部署数据库按顺序执行 `002-contract-review.sql`、`003-contract-extraction.sql`、`004-contract-confirmation.sql`。

- `contract_review_tasks.extraction_result`：脱敏条款、事实和合同证据。
- `contract_review_tasks.confirmation_result`：当前有效事实快照，包含用户值、有效来源和确认状态。
- `contract_review_tasks.confirmation_revision`：事实确认的乐观锁版本。
- `contract_review_fact_confirmations`：每次用户动作的追加式审计事件，不覆盖历史记录。

确认表只保存脱敏证据和用户确认元数据；原始合同仍在私有文件存储中，不进入公共 Qdrant。

## 已知问题

- 数据目录与 PG 版本绑定，升级镜像前需清空 `./postgres_data`
- 旧 PG14 数据在 PG17 镜像下报 `incompatible with server`
