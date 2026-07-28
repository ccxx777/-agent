# Backend API：FastAPI + LangGraph

> 本文描述当前源码已经挂载的接口。合同审查 Workflow 的法律规则节点尚未实现，当前合同接口只负责上传、解析、脱敏和质量状态。

| 服务 | 端口 | 职责 |
|---|---:|---|
| `backend` | `8000`（默认只绑定宿主机 `127.0.0.1`） | FastAPI、LangGraph、检索、会话和合同任务 |

## 通用约定

- 需要登录的接口使用 `Authorization: Bearer <token>`。
- JSON 使用 UTF-8；文件上传使用 `multipart/form-data`。
- 合同接口不返回私有存储路径，不把原始合同写入公共 Qdrant 或 `data_worker`。
- 生产检索 Collection 由 `RAG_COLLECTION` 配置；评测 Collection 由评测脚本显式传入。

## GET /health

健康检查不依赖完整业务链路：

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status": "ok"}
```

## POST /api/chat

非流式问答接口。Frontend 的 Nginx 可以代理到该路径；评测脚本也通过同一 Backend 边界验证 Agent/RAG 主链。

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is greedy decoding?","session_id":"s1","user_id":"dev"}'
```

请求字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | `string` | 用户问题 |
| `session_id` | `string` | 会话/ LangGraph thread 标识 |
| `user_id` | `string` | 用户标识；生产环境来自认证上下文 |

典型响应：

```json
{
  "answer": "回答正文，包含可追溯引用",
  "session_id": "s1",
  "contexts": ["实际用于生成的上下文"],
  "documents": [{"rank": 1, "doc_id": "...", "title": "..."}]
}
```

### LangGraph 执行路径

```text
START → condense_memory → chatbot → tools/search_knowledge_base
      → RetrievalService → generate_answer → END
```

`RetrievalService` 是 Agent、Eval API 和离线基线共享的唯一检索业务入口。答案生成必须遵守“仅基于上下文、证据不足时明确拒答”的约束。

## GET /api/chat/history/{session_id}

从 PostgreSQL Checkpoint 中读取会话历史，仅返回 `human` 和 `ai` 消息，不返回 Tool/System 消息。

```bash
curl http://127.0.0.1:8000/api/chat/history/s1 \
  -H "Authorization: Bearer $TOKEN"
```

## POST /api/contract-reviews

创建一份合同解析任务，返回 HTTP `202`。接口要求登录，文件先保存到私有目录，再由后台任务解析。

```bash
curl -X POST http://127.0.0.1:8000/api/contract-reviews \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@劳动合同.docx"
```

### 输入限制

- 支持 `.pdf`、`.doc`、`.docx`，大小不超过 20 MB，页数不超过 50 页。
- PDF 会校验 `%PDF-` 文件头，不能只相信扩展名。
- DOCX 读取正文和表格；DOC 通过容器内 `antiword` 提取文本。
- Word 文档不能稳定恢复原始分页、页眉页脚和浮动文本框，可能产生 `format_page_boundary_unavailable` 质量标记。
- 扫描 PDF 的 OCR 默认关闭；启用后，外部 OCR 原图发送会记录在 `privacy.external_raw_image_sent`。

### 202 响应示例

```json
{
  "review_id": "2f2d7d32-1f2d-4bb0-a6c4-2e3c8e21b9e1",
  "status": "queued",
  "filename": "劳动合同.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "size_bytes": 183204,
  "sha256": "...",
  "page_count": 8
}
```

上传接口只创建任务，不直接返回合同正文。非法格式、空文件、超限文件和 PDF 魔数不正确时返回 `400`；服务异常返回 `500`。

## GET /api/contract-reviews/{review_id}

查询合同任务状态。只有同一用户可以读取任务；不存在或无权访问时返回 `404`。

状态机：

```text
queued → extracting → ready
                    ↘ needs_confirmation
                    ↘ failed
```

### 响应字段

| 字段 | 说明 |
|---|---|
| `status` | `queued`、`extracting`、`ready`、`needs_confirmation` 或 `failed` |
| `quality` | 页数、文本覆盖率、OCR 页、失败页和可疑页 |
| `privacy` | 脱敏版本、类别计数、零宽字符计数和外部原图审计字段 |
| `pages` | 逐页脱敏文本、页面模式、OCR 标记和质量标记 |
| `extraction_status` | `not_started`、`running`、`ready`、`needs_confirmation` 或 `failed`；与文件解析 `status` 独立 |
| `extraction` | 条款、结构化事实、证据页码/字符偏移、确认问题和警告；只包含脱敏文本 |
| `error_message` | 面向用户的安全错误说明，不包含原始合同内容 |

`needs_confirmation` 表示文本层不足、OCR 未配置/失败或页边界无法可靠恢复；它不等于“合同没有风险”。

## 合同隐私处理

在文本进入 Embedding、Reranker、LLM 或日志之前执行：

- 身份证号：保留前 6 位和后 4 位；
- 手机号：保留前 3 位和后 4 位；
- 银行卡号：保留前 4 位和后 4 位；
- 先清理零宽空格、零宽连接符、BOM 和不可见控制字符，避免敏感号码被隐藏字符打断；
- 普通合同文字原样保留，不做全局模糊化。

原始文件、解析结果和报告应使用私有存储与短期保留策略；公共日志、评测 JSON 和 GitHub 内容不得出现真实合同或敏感信息。

## 配置与数据库迁移

合同模块相关配置包括：

- `CONTRACT_STORAGE_DIR`
- `CONTRACT_MAX_UPLOAD_BYTES`
- `CONTRACT_MAX_PAGES`
- `CONTRACT_DOC_COMMAND`
- `CONTRACT_DOCUMENT_TIMEOUT`
- `CONTRACT_OCR_ENABLED`、`CONTRACT_OCR_BASE_URL`、`CONTRACT_OCR_API_KEY`、`CONTRACT_OCR_MODEL`
- `CONTRACT_EXTRACTION_ENABLED`（默认 `false`）、`CONTRACT_EXTRACTION_BATCH_CLAUSES`（默认 `6`）、`CONTRACT_EXTRACTION_MAX_CHARS`（默认 `12000`）

旧 PostgreSQL 需要执行 `backend/sql/migrations/002-contract-review.sql` 和 `backend/sql/migrations/003-contract-extraction.sql`；新建数据库会执行 `backend/sql/init/03-contract-review.sql`。

## 运行与验证

普通后端源码修改后：

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml restart backend
curl http://127.0.0.1:8000/health
```

修改依赖或 Dockerfile 才需要重建 Backend。RAGAS 依赖留在 `evaluation/requirements.txt`，不安装进生产镜像。合同模块上线前至少应分别用 PDF、DOC、DOCX 验证成功、失败和隐私脱敏路径。
