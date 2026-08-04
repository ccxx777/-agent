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

## 6. A 级劳动合同法律检索门禁

固定题集位于 `evaluation/legal_labor_contract_questions.json`，共 10 道问题，覆盖书面
合同、必备条款、试用期、工资、解除、经济补偿、双倍工资、加班和社会保险等主题。脚本
不仅检查“有返回”，还要求每道题在最终结果中命中预期的 `doc_id + article_no`，并验证：

- `source_level=A` 且 `citation_eligible=true`；
- `official_url` 以 `https://flk.npc.gov.cn/` 开头；
- `effective_date` 与题集期望日期一致；
- `citation_label` 包含预期条号；
- 实际返回的可引用片段包含题集要求的法律关键词；
- 法律资料状态必须为 `ACTIVE`；只有临时 staging 才允许显式使用 `--allow-pending-governance`。

如果未来新建的资料仍是 `PENDING_LEGAL_REVIEW`，只能使用以下 staging 命令，不能切换生产配置：

```bash
docker exec backend python /app/evaluation/legal_retrieval_smoke.py \
  --collection legal_labor_a_v1 \
  --qdrant-url http://db_qdrant:6333 \
  --embed-url http://embedding_service:8001/embed \
  --allow-pending-governance \
  --output data/legal/labor_contract/results/legal_retrieval_smoke_v1.json
```

### 历史 staging 实测（已归档）

2026-08-04 在服务器上对 `legal_labor_a_v1` 执行上述固定题集，结果为：

| 项目 | 结果 |
|---|---:|
| 固定问题 / 通过 / 失败 | 10 / 10 / 0 |
| Gold Hit@1 / Hit@3 | 80.00% / 100.00% |
| 总耗时 / 平均耗时 | 24.729 秒 / 约 2.47 秒/题 |
| 引用与治理字段 | 10/10 均通过 `source_level=A`、`citation_eligible=true`、官方 URL、生效日期和引用片段检查 |
| 运行参数 | `--allow-pending-governance`（仅 staging） |

两道题（`labor_legal_03`、`labor_legal_08`）的预期法条位于第 2 名，但都在最终 Top-3 内，因此没有失败。该结果仅代表当时 staging 的技术检索通过；随后已完成治理确认、payload 修复和正式激活。

### 最近一次服务器实测（正式 ACTIVE）

法律资料已统一为 `ACTIVE`，Backend 使用 `LEGAL_A_ALLOW_PENDING_GOVERNANCE=false`，
命令不再携带 `--allow-pending-governance`。正式结果为：10/10 通过、Gold Hit@1=80.00%、
Gold Hit@3=100.00%、0 失败；每题均通过 A 级来源、官方 URL、生效日期和可引用片段检查。

脚本的纯函数测试位于 `evaluation/test_legal_retrieval_smoke.py`。输出 JSON 只保存问题、
预期字段、检索元数据和截断后的法律引用片段，不保存合同原文。

### 6.1 合同审查 Workflow 法律引用端到端回归

法律检索题集通过后，再用一份脱敏或自拟劳动合同验证真实产品链路：
上传 → 事实提取 → 确认门禁 → 合同审查 Workflow → 报告 JSON/PDF → 报告问答。
这一步会额外检查报告中的每条 A 级法律来源是否包含可追溯引用字段，不会把合同正文或引用原文写入 Smoke 输出。

先在 Backend 的 `.env` 中配置独立法律 Collection：

```env
LEGAL_A_COLLECTION=legal_labor_a_v1
LEGAL_A_ALLOW_PENDING_GOVERNANCE=false
```

使用脱敏/自拟 DOCX 运行：

```bash
uv run --with httpx --with pypdf python evaluation/contract_review_e2e.py \
  --file /root/my-ai-research/test_contract/劳动合同.docx \
  --base-url http://127.0.0.1:8000 \
  --token "$TOKEN" \
  --resolution-policy supplement \
  --ack-test-confirmation-writes \
  --require-legal-citations \
  --output data/contract_legal_workflow_e2e.json
```

通过标准：Workflow 返回 `completed` 或有明确警告的 `partial`；`legal_sources` 非空，
每条来源均为 `source_level=A`、`citation_eligible=true`，官方 URL 以
`https://flk.npc.gov.cn/` 开头，具有 `effective_date`、包含“第…条”的
`citation_label`、非空 `quote`，且激活状态为 `ACTIVE`。脚本输出中的 `legal_citations`
只包含计数和状态，不包含法条正文。

正式回归已经去掉 `--allow-pending-legal-governance`，并确认
`LEGAL_A_ALLOW_PENDING_GOVERNANCE=false`；只修改 `.env` 时重启 Backend 即可，不需要重建镜像。
最近一次 E2E 结果为 `workflow_status=completed`、`findings=2`、`legal_sources=6`、
报告问答通过、删除后 404、隐私哨兵 0、`external_ocr=false`，总耗时约 103.99 秒。

### 6.2 A 级法律资料从 PENDING 到 ACTIVE（已执行；后续替换资料可复用）

本节记录已完成的激活与 payload 修复过程；以后替换资料时仍必须同时更新三处状态：prepared artifact、每个 Qdrant point 的
`legal_activation_status`，以及 Backend 的治理开关。不要直接编辑 JSON 或 Qdrant；使用
仓库提供的激活工具，它会做只读 preflight、要求三项人工确认、创建备份，并在写入后逐点验证。
该工具应在服务器宿主机项目目录执行，不能在 `sentinel` 容器中执行（容器对
`/app/data/legal` 是只读挂载）。

先做只读门禁：

```bash
cd /root/my-ai-research
uv run --index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with-requirements data_worker/requirements.txt \
  python evaluation/legal_labor_activation.py preflight \
  --base data/legal/labor_contract \
  --qdrant-url http://127.0.0.1:6333 \
  --collection legal_labor_a_v1
```

确认输出 `status=ready`、Point 数为 477，且没有 `static_errors` 后，再执行不写入的
激活演练：

```bash
uv run --index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with-requirements data_worker/requirements.txt \
  python evaluation/legal_labor_activation.py activate \
  --base data/legal/labor_contract \
  --qdrant-url http://127.0.0.1:6333 \
  --collection legal_labor_a_v1 \
  --reviewer ccxx \
  --review-note "已核对国家法律法规数据库来源、全国适用范围、生效状态和正文一致性" \
  --confirm-national-scope \
  --confirm-effective-status \
  --confirm-content-match
```

演练输出 `status=dry_run_ready` 后，才在维护窗口执行实际激活。`--apply` 是唯一会
修改本地 artifact 和 Qdrant 的开关：

```bash
uv run --index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with-requirements data_worker/requirements.txt \
  python evaluation/legal_labor_activation.py activate \
  --base data/legal/labor_contract \
  --qdrant-url http://127.0.0.1:6333 \
  --collection legal_labor_a_v1 \
  --reviewer ccxx \
  --review-note "已核对国家法律法规数据库来源、全国适用范围、生效状态和正文一致性" \
  --confirm-national-scope \
  --confirm-effective-status \
  --confirm-content-match \
  --apply
```

成功后应看到 `status=activated` 和备份目录。随后检查 manifest 与 Qdrant：

如果激活后检查发现 Qdrant Point 只剩 `legal_activation_status`、缺少
`source_level`/`citation_eligible`/法条号等字段，不要重新计算 Embedding，也不要删除
Collection；使用 payload 修复工具。该工具会先创建 Qdrant snapshot，再从 ACTIVE
prepared artifact 逐 Point 使用合并接口恢复引用元数据和 fulltext 字段：

```bash
uv run --index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with-requirements data_worker/requirements.txt \
  python evaluation/legal_labor_payload_repair.py \
  --base data/legal/labor_contract \
  --qdrant-url http://127.0.0.1:6333 \
  --collection legal_labor_a_v1
```

输出必须为 `status=repaired`，并显示 `payload_fields_verified`。修复后再运行两个正式门禁。

```bash
python -c "import json; p='data/legal/labor_contract/prepared/a_level/manifest.json'; m=json.load(open(p, encoding='utf-8')); print(m['status'], m['governance']['legal_activation_status'], m['activation']['reviewer'])"
curl --fail --silent --show-error -X POST \
  -H 'Content-Type: application/json' \
  -d '{"limit":1,"with_payload":["legal_activation_status"],"with_vector":false}' \
  http://127.0.0.1:6333/collections/legal_labor_a_v1/points/scroll
```

在服务器 `.env` 中设置正式治理开关，然后强制重建容器配置（不需要重建镜像）：

```env
LEGAL_A_COLLECTION=legal_labor_a_v1
LEGAL_A_ALLOW_PENDING_GOVERNANCE=false
```

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml \
  up -d --no-build --force-recreate backend
```

最后运行两个正式门禁，命令中不要再出现 `--allow-pending-governance` 或
`--allow-pending-legal-governance`：

```bash
docker exec backend python /app/evaluation/legal_retrieval_smoke.py \
  --collection legal_labor_a_v1 \
  --qdrant-url http://db_qdrant:6333 \
  --embed-url http://embedding_service:8001/embed \
  --output /app/data/legal/labor_contract/results/legal_retrieval_smoke_active.json

uv run --index https://pypi.tuna.tsinghua.edu.cn/simple \
  --with httpx --with pypdf \
  python evaluation/contract_review_e2e.py \
  --file /root/my-ai-research/test_contract/劳动合同.docx \
  --base-url http://127.0.0.1:8000 \
  --token "$TOKEN" \
  --resolution-policy supplement \
  --ack-test-confirmation-writes \
  --require-legal-citations \
  --output data/contract_legal_workflow_e2e_active.json
```

最近一次执行结果：法律检索 10/10、合同审查 E2E 通过，`legal_labor_a_v1` 的
artifact/Qdrant/Backend 治理状态均为 `ACTIVE`。任一后续资料替换门禁失败，都应保持
`LEGAL_A_COLLECTION` 不变，并根据激活工具输出的备份目录恢复 artifact；不要用
`--allow-pending-*` 绕过正式门禁。

## 通过标准

迁移检查、三格式 API Smoke、端到端验收、法律检索门禁、隐私哨兵、删除验证均通过，且
Backend/Embedding/Qdrant/PostgreSQL 健康检查正常，才可把 PLAN 中对应门禁标记为完成。
