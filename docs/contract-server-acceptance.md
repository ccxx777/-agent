# 合同服务端验收 Runbook

本文档用于验证合同报告迁移和 PDF/DOC/DOCX 上传链路。它不自动执行数据库迁移，也不会打印合同原文、文件路径或敏感值。

## 服务器执行前提

先进入项目目录并确认工作区干净、远端指向正确：

```bash
cd /root/my-ai-research
git status --short
git remote -v
```

如果存在服务器本地未提交文件，先停止，不要用 `reset`、强制拉取或覆盖操作。

## 1. 执行 005/006/007 迁移

只在确认依赖迁移已按顺序执行且数据库已备份后运行：

```bash
docker exec -i db_pg psql -v ON_ERROR_STOP=1 -U admin -d ai_assistant < backend/sql/migrations/005-session-contract-report.sql
docker exec -i db_pg psql -v ON_ERROR_STOP=1 -U admin -d ai_assistant < backend/sql/migrations/006-contract-retention.sql
docker exec -i db_pg psql -v ON_ERROR_STOP=1 -U admin -d ai_assistant < backend/sql/migrations/007-session-conversation-scope.sql
```

迁移文件使用 `IF NOT EXISTS`，可重复执行；`007` 会把旧 session 标记为需要一次性范围检查。执行输出中不能出现 `ERROR` 或 `ROLLBACK`。

## 2. 只读检查迁移结果

在 backend 容器中运行，使用容器内已有的 PostgreSQL 配置：

```bash
docker exec backend python /app/evaluation/contract_migration_check.py \
  --host db_pg \
  --port 5432 \
  --user admin \
  --database ai_assistant \
  --output /app/data/contract_migration_check.json
```

结果必须为 `"status": "passed"`。该检查证明目标 schema 已满足，不代替保存迁移命令输出。

## 3. PDF/DOC/DOCX API 回归与隐私门禁

将三种脱敏测试样本放在服务器宿主机目录，例如 `/root/my-ai-research/test_contract/`。该目录默认没有挂载进 Backend 容器，因此上传 Smoke 要在服务器项目宿主机执行，而不是用 `docker exec backend` 访问 `/app/test_contract/`。不要把真实合同放入 Git 或输出目录。然后运行：

```bash
uv run --with httpx python evaluation/contract_upload_api_smoke.py \
  --file pdf=/root/my-ai-research/test_contract/劳动合同.pdf \
  --file doc=/root/my-ai-research/test_contract/劳动合同.doc \
  --file docx=/root/my-ai-research/test_contract/劳动合同.docx \
  --base-url http://127.0.0.1:8000 \
  --token "$TOKEN" \
  --require-extraction \
  --allow-external-ocr \
  --privacy-sentinel "测试手机号" \
  --privacy-sentinel "测试身份证号" \
  --expect-redaction phone=1 \
  --expect-redaction id_card=1 \
  --output /app/data/contract_upload_api_smoke.json
```

`--privacy-sentinel` 必须替换为测试样本中实际出现的敏感值；脚本不会在结果中回显这些值。当前脚本要求 PDF、DOC、DOCX 三种格式在一次运行中同时提供，`--expect-redaction` 是对每种格式统一执行的计数门禁；如果三份样本的敏感字段数量不同，应先统一测试样本，或暂不使用该计数参数。

只有当 Backend 的 `CONTRACT_EXTRACTION_ENABLED=true` 时才添加 `--require-extraction`；如果只验收文件解析、脱敏和迁移 API，可暂时省略该参数。

脚本会检查：

- 三种扩展名均能上传并进入解析终态；
- `session_id`、`retention_policy`、`expires_at` 可读，验证会话和留存字段；
- 合同历史、会话历史、会话合同列表 API 均可读；
- API JSON 不包含 `storage_path`、原文等私有字段；
- 隐私哨兵不出现在任何 API JSON；
- 默认门禁要求 `external_raw_image_sent=false`。本目录的 PDF 含低文字量页面，会触发已配置的外部 OCR，因此本次样本必须显式使用 `--allow-external-ocr`；结果仍必须如实记录 `external_raw_image_sent=true`。如果产品策略禁止原图外发，应关闭 OCR 或换用所有页面均为原生文字层的 PDF，再去掉该参数；
- 默认删除 Smoke 任务，并确认删除后查询返回 404。

如果需要保留任务供人工查看，添加 `--keep-reviews`；验收结束后必须手动删除。

## 4. 解析失败时的诊断

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker logs --tail 150 backend
docker exec backend sh -lc 'command -v antiword && antiword --version || true'
```

DOC 失败通常是容器内缺少 `antiword` 或文档本身损坏；不要因为单个 DOC 失败而重建 embedding 镜像。只在 Backend 依赖或 Dockerfile 变更时重建 Backend。

## 5. 合同审查端到端验收

上传 Smoke 只证明文件解析、隐私边界和删除闭环；它不会执行事实确认、法律审查
Workflow、报告持久化或报告问答。需要用一份脱敏/自拟劳动合同单独运行端到端门禁：

```bash
uv run --with httpx --with pypdf python evaluation/contract_review_e2e.py \
  --file /root/my-ai-research/test_contract/劳动合同.docx \
  --base-url http://127.0.0.1:8000 \
  --token "$TOKEN" \
  --resolution-policy supplement \
  --ack-test-confirmation-writes \
  --privacy-sentinel "测试手机号" \
  --privacy-sentinel "测试身份证号" \
  --output data/contract_review_e2e.json
```

运行端到端门禁前必须确认 Backend 已启用 `CONTRACT_EXTRACTION_ENABLED=true`，并且
`MAIN_MODEL`/模型服务配置可用；否则脚本会在文件解析完成且提取仍为 `not_started`
时快速失败，而不是等待完整超时。`--resolution-policy supplement` 只向测试合同的
未确认事实写入固定的非敏感测试值，因此必须显式传入 `--ack-test-confirmation-writes`。
该参数代表你确认本次只使用脱敏/自拟合同；绝不能把它用于真实用户合同。

如果使用 PDF 且该 PDF 触发外部 OCR，必须显式增加 `--allow-external-ocr`；若还要验证
外部 OCR 审计确实发生，再增加 `--expect-external-ocr`，脚本会严格要求
`external_raw_image_sent=true`；DOC/DOCX 测试不需要这两个参数。默认行为会在验收结束
后删除测试任务；如需人工查看可临时使用 `--keep-review`，查看后必须手动删除。

端到端脚本只输出元数据摘要，不输出报告正文、合同原文、证据引文或模型答案。它按
顺序检查：

- 提取终态和公开响应隐私边界；
- 确认快照的 revision、自动确认/补充动作和 `ready_for_legal_review` 门禁；
- Workflow 返回 `completed` 或 `partial`，并返回持久化 `report_id`；
- 报告 JSON 可按 `review_id` 查询，且保持同一 `session_id`；
- 报告 PDF 的内容类型、`%PDF` 文件头，并检查隐私哨兵未写入 PDF 字节；
- `contract_review` 模式报告问答返回非空答案并保持会话；
- 会话历史与会话合同列表包含当前任务；
- 删除后查询返回 404。

脚本的纯函数测试位于 `evaluation/test_contract_review_e2e.py`，服务器实测前先在
本地运行该测试，避免把真实合同发送到错误的环境。

## 通过标准

迁移检查、三格式 API Smoke、端到端验收、隐私哨兵、删除验证均通过，且
Backend/Embedding/Qdrant/PostgreSQL 健康检查正常，才可把 PLAN 中对应门禁标记为完成。
