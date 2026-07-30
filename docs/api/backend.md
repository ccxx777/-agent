# Backend API：FastAPI + LangGraph

> 本文描述当前源码已经挂载的接口。合同接口已覆盖上传、解析、脱敏、事实提取、事实确认和 Workflow v0.1；法律资料导入、规则卡片专家复核、报告持久化和前端展示仍在进行中。

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

## GET /api/contract-reviews/{review_id}/confirmation

读取事实确认表单。响应中的 `original_value` 是模型在脱敏合同上的原始提取结果，`user_value` 是用户输入，`effective_value` 才是后续规则层允许读取的值；三者不会互相覆盖。

```bash
curl http://127.0.0.1:8000/api/contract-reviews/$REVIEW_ID/confirmation \
  -H "Authorization: Bearer $TOKEN"
```

`questions` 与 `unresolved_questions` 是绑定到 `fact_id` 的结构化问题。只有事实提取完成后才会有可确认内容；提取尚未完成时返回 `409`。

## PUT /api/contract-reviews/{review_id}/confirmation

保存确认草稿或提交确认。`base_revision` 必须等于当前响应中的 `confirmation_revision`，否则返回 `409`，客户端应重新读取表单。`request_id` 可选，但建议由客户端为每次提交生成稳定值，以支持网络重试幂等。

```bash
curl -X PUT http://127.0.0.1:8000/api/contract-reviews/$REVIEW_ID/confirmation \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "base_revision": 0,
    "submit": true,
    "request_id": "confirm-term-20260730-01",
    "items": [
      {"fact_id": "fact_002", "action": "supplement", "value": "12个月"}
    ]
  }'
```

每条 `items` 只允许以下五种动作：

| `action` | 行为 | 证据与有效来源 |
|---|---|---|
| `confirm` | 接受原始提取值 | 必须已有合同证据，来源为 `contract` |
| `correct` | 用户指出合同中的正确值 | 服务会在脱敏合同中重新定位；找不到时返回 `422`，提示改用 `supplement` |
| `supplement` | 合同缺失，由用户补充背景事实 | 保存 `user_value`，来源为 `user`，不生成合同证据 |
| `not_applicable` | 明确该字段对本合同不适用 | 有效值为空，来源为 `none` |
| `defer` | 暂不确认 | 保留待办，不允许进入最终法律结论 |

提交成功后，`confirmation_status` 为 `pending`、`in_progress` 或 `completed`；只有 `completed` 且没有 `unresolved_questions` 时，`ready_for_legal_review` 才为 `true`。确认层不替用户决定是否签署合同。

## POST /api/contract-reviews/{review_id}/workflow

运行首版劳动合同审查 Workflow。该接口不会重新读取原始文件，而是读取经过证据定位和用户确认的事实快照。

```bash
curl -X POST http://127.0.0.1:8000/api/contract-reviews/$REVIEW_ID/workflow \
  -H "Authorization: Bearer $TOKEN"
```

执行路径为：事实加载 → 确认门禁 → 全国劳动合同范围检查 → A 级法律检索 → 确定性规则卡片 → B 级官方案例补充 → 结构化报告。

### 状态和安全行为

| `workflow_status` | 含义 |
|---|---|
| `awaiting_confirmation` | 事实尚未确认，只返回 `pending_questions`，不生成确定性风险结论 |
| `completed` | 规则、法律检索和可选案例节点正常完成 |
| `partial` | 资料库未配置或检索失败；保留事实层提示并显示 `warnings` |
| `out_of_scope` | 当前版本无法识别为全国通用劳动合同 |

响应示例：

```json
{
  "review_id": "...",
  "workflow_status": "partial",
  "report": {
    "scope": "labor_contract_national",
    "findings": [
      {
        "rule_id": "LC-010",
        "risk_level": "high",
        "finding_type": "possible_conflict",
        "summary": "合同出现不缴纳或放弃社会保险的表述。",
        "legal_references": ["社会保险法第四条、第五十八条、第六十条"],
        "recommendation": "确认实际参保主体、参保地和缴费记录。"
      }
    ],
    "pending_questions": [],
    "legal_sources": [],
    "case_sources": [],
    "warnings": ["A 级资料检索暂时不可用"]
  }
}
```

报告中的 `risk_level` 是当前证据下的提示强度，不等于司法结论；`disclaimer` 明确说明不构成律师意见、签署决定或结果担保。原始合同仍只保存在私有目录，不进入法律资料 Collection。

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
- `LEGAL_A_COLLECTION`（默认 `legal_labor_a_v1`）：全国通用 A 级法律资料
- `LEGAL_B_COLLECTION`（默认 `legal_labor_b_v1`）：官方 B 级案例资料

旧 PostgreSQL 需要按顺序执行 `backend/sql/migrations/002-contract-review.sql`、`backend/sql/migrations/003-contract-extraction.sql` 和 `backend/sql/migrations/004-contract-confirmation.sql`；新建数据库会执行 `backend/sql/init/03-contract-review.sql`。

## 运行与验证

普通后端源码修改后：

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml restart backend
curl http://127.0.0.1:8000/health
```

修改依赖或 Dockerfile 才需要重建 Backend。RAGAS 依赖留在 `evaluation/requirements.txt`，不安装进生产镜像。合同模块上线前至少应分别用 PDF、DOC、DOCX 验证成功、失败和隐私脱敏路径。
