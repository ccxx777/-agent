# 劳动合同风险审查助手：开发计划与验收门禁

> 更新日期：2026-07-28
>
> 当前阶段：通用 RAG 已完成 v2 检索门禁和端到端基线；合同上传（PDF/DOC/DOCX）、私有存储、异步任务、质量状态、隐私脱敏、条款切分和事实提取基础链路已实现，正在进行服务器运行时验证。

## 一、产品目标与边界

首版面向中国大陆个人用户的劳动合同签署前辅助审查。产品输出风险事实、风险等级、法律依据、官方案例解释、修改建议和需要用户补充的事实，不替用户决定是否签署，不提供地方性法律判断，不构成律师意见或责任担保。

首版资料优先级：

1. **A 级**：现行有效的全国通用法律、行政法规、司法解释和官方规范性文件。
2. **B 级**：最高人民法院指导案例、典型案例和可公开核验的裁判文书。
3. 暂不接入律师公众号、转载文章和付费/授权不明确的行业资料。

## 二、架构分工

### 保留并作为生产基础

- FastAPI + LangGraph：API 边界和可暂停、可恢复的 Workflow 编排。
- `RetrievalService` + Qdrant Cascade Funnel：统一服务 Agent、Eval 和离线基线。
- BGE-M3 Embedding Service：Dense 和 Sparse 向量生成。
- PostgreSQL：用户、会话、Checkpoint、文档指纹和合同任务状态。
- 独立 Data Worker：公共资料的 Loader、Chunker、Embedding、Writer 和指纹幂等。
- 私有合同存储、页级解析、脱敏和质量门禁：合同原文不进入公共 RAG Collection。

### 已退出当前生产路径

- Component Registry、YAML Pipeline、旧 Backend Sentinel。
- Redis 及其旧消息/Token 表。
- 重复的旧 RRF `run()` 入口和只用于历史解释的实验包装器。

### 明确隔离

- `evaluation/` 独立于 Backend 和 Frontend，通过真实 API/服务边界运行评测。
- `data/` 只保存本地语料和评测产物，默认被 Git 忽略。
- `watsonx_docsqa_colab_v1/v2` 只用于离线评测；生产通用库仍由 `RAG_COLLECTION=rag_chunks` 控制。

## 三、已完成工作

### 3.1 通用 RAG 主链

- [x] LangGraph 图、工具调用、答案生成和引用约束稳定运行。
- [x] `RetrievalPayload` 统一返回 `context`、`contexts` 和最终排序 `documents`。
- [x] Dense、BGE-M3 Sparse、BM25 三路 L1 召回，L2 动态融合粗排，L3 云端 Reranker 精排 Top-3。
- [x] 中英文 Query Specificity 使用命中密度计算，统一将 `S` 截断到 `[0.2, 0.8]`。
- [x] Data Worker 使用 SHA-256 指纹实现增量同步、断点恢复和重复文件跳过。

### 3.2 检索 v2 迁移与门禁

- [x] Qdrant 从 1.9.0 升级至 1.10.1，并完成旧 Collection 快照校验。
- [x] `watsonx_docsqa_colab_v2` 离线写入 6759 Points，使用原生 Sparse/BM25 结构。
- [x] v1/v2 在相同 30 道问题、相同最终 Top-3 下通过门禁：Hit@3 不下降。
- [x] 当前门禁结果：Hit@1 `83.33%`、Hit@3 `90.00%`、MRR@3 `86.67%`、Mean Recall@3 `90.00%`。
- [x] 平均检索延迟由 `6.884s` 降至 `1.038s`，约提升 `6.63×`。
- [x] `test_3`、`test_5`、`test_8` 作为共同未命中和回归样本保留；`test_8` 不是 v2 迁移引入的退化。

### 3.3 端到端生成与 RAGAS

- [x] 30/30 答案生成完成，无结构性失败。
- [x] v2 生成基线：Gold Hit@1/Hit@3 为 `83.33% / 90.00%`，拒答 3 题，平均总延迟 `5.938s`，P95 `9.513s`。
- [x] RAGAS 0.4.3 完整覆盖 30 题：Answer Correctness `0.653255`、Faithfulness `0.908333`、Context Relevance `0.891667`，覆盖率均为 100%。
- [x] 已保留人工重点抽查和 RAGAS 明细，不用单个 LLM Judge 均值替代人工复核。

### 3.4 合同上传基础模块

- [x] `POST /api/contract-reviews`：登录后上传 PDF、DOC、DOCX，限制 20 MB、50 页。
- [x] 原始文件按 `review_id` 保存到私有目录，并写入 PostgreSQL 任务记录和 SHA-256。
- [x] `GET /api/contract-reviews/{review_id}`：查询任务状态并返回脱敏后的页级结果。
- [x] PDF 使用 PyMuPDF；DOCX 读取 OOXML 正文和表格；DOC 使用 `antiword`。
- [x] 状态机 `queued → extracting → ready / needs_confirmation / failed`。
- [x] 脱敏身份证号、手机号、银行卡号；处理零宽字符和不可见控制字符；只记录脱敏统计，不记录敏感值。
- [x] 扫描 PDF OCR 接口和外部原图发送审计字段已预留，默认关闭。
- [x] 后端合同相关测试、解析测试和隐私测试已加入 `backend/tests/`。

### 3.5 条款与事实提取基础链路

- [x] `contract_extraction.py`：定义条款、事实、证据片段、事实状态和提问结果 Schema。
- [x] `ContractClauseSplitter`：按编号标题和劳动合同常见标题做确定性切分，并保留页码范围。
- [x] `StructuredContractFactExtractor`：只向模型发送脱敏条款，要求 JSON 候选事实，不输出法律风险结论。
- [x] `EvidenceLocator`：在脱敏页文本中重新定位模型引用，记录页码、字符偏移和匹配方式。
- [x] `ContractFactNormalizer`：本地清理字段、检查缺证据/低置信度、发现同名事实冲突并生成确认问题。
- [x] PostgreSQL 保存 `extraction_status` 与 `extraction_result`；文件解析状态和事实提取状态相互独立。
- [x] 默认关闭外部事实提取模型调用，设置 `CONTRACT_EXTRACTION_ENABLED=true` 后才启用。

## 四、当前进行中

### P0：完成上传模块的服务器验收

- [ ] 在服务器重建/重启 Backend 后，用真实 PDF、DOC、DOCX 分别上传。
- [ ] 验证 `queued → extracting → ready` 和异常时的 `failed` 路径。
- [ ] 验证 DOC 容器内 `antiword` 可执行；若本机 Windows 没有该运行时，先转换为 PDF/DOCX。
- [ ] 检查真实合同不会进入 `data_worker`、Qdrant 或公开评测结果。
- [ ] 检查脱敏前后字段、零宽字符计数、日志和 API 响应均不泄露原始敏感值。

### 3.6 事实确认基础模块

- [x] 新增 `contract_confirmation.py`：定义五类用户动作、确认状态、有效来源、结构化问题、revision 请求和表单响应。
- [x] 扩展 `ContractFact`：保留原始 `value`，并分层保存 `user_value`、`effective_value`、`effective_source` 和确认状态。
- [x] 新增 `ContractFactConfirmationService`：提供确认、合同内修正、用户补充、不适用和暂不确认的确定性状态转换。
- [x] `correct` 必须通过本地 `EvidenceLocator` 在脱敏合同中重新找到证据；找不到时不能伪造合同事实。
- [x] 新增 `GET/PUT /api/contract-reviews/{review_id}/confirmation`，使用 `base_revision` 乐观锁和 `request_id` 幂等重试。
- [x] 新增 `004-contract-confirmation.sql` 及初始化表结构，保存确认快照和追加式事件审计。
- [x] 只有必答问题解决并显式提交后才允许 `ready_for_legal_review=true`；事实确认不输出法律风险结论。
- [x] 新增确认层单元测试，覆盖五类动作、证据失败、原始值保留、版本冲突和幂等。

### P1：合同审查 Workflow 第一版

- [x] 建立劳动合同条款与事实 Schema：主体、期限、试用期、工作地点、岗位、薪资、工时休息、社保、解除、违约金、竞业、保密等。
- [x] 完成“条款切分 → 候选事实 → 本地证据定位 → 缺失/矛盾确认”的第一版链路。
- [ ] 将“条款提取、事实确认、检索、规则计算、报告表达”拆成清晰的 LangGraph 节点。
- [ ] 信息不足时暂停并提出补充问题；事实未确认前不得输出确定性高风险结论。
- [ ] 先实现全国通用规则，不加入地方性判断。
- [ ] 报告字段固定为：风险事实、风险等级、判定依据、相关条款、可选案例、修改建议、待确认事项、免责声明。

### P1：法律资料准备

- [ ] 按 [`docs/labor-contract-legal-corpus-plan.md`](docs/labor-contract-legal-corpus-plan.md) 收集 P0 法律最小包、建立版本 metadata 和导入 manifest。
- [ ] 收集并核验 A 级法律和司法解释的版本、生效日期、来源 URL、废止状态。
- [ ] 收集 B 级官方案例，保留案号、法院、裁判日期、争议焦点和官方来源。
- [ ] 为每条规则建立人工复核卡：适用前提、例外条件、证据需求和预期输出。
- [ ] 数据入库前做版权、授权、转载限制和个人信息检查。

## 五、下一阶段验收门禁

### 上传与隐私门禁

- PDF、DOC、DOCX 均能创建任务，非法扩展名、空文件、超限文件均被拒绝。
- 解析结果只包含脱敏页文本和质量元数据；原始文件路径不通过 API 返回。
- 解析失败可查询、可重试或明确失败，不留下孤立公共向量。
- 任意测试日志、评测 JSON、GitHub Issue 和公开报告都不含真实合同原文。

### RAG 评测门禁

- 固定问题集 30/30 完成且无错误；v2 Hit@3 不低于当前 v1 基线。
- 同时记录 Hit@1、Hit@3、MRR@3、Mean Recall@3、Mean/P50/P95 延迟和零命中题。
- 端到端生成必须保存输入 SHA-256、Collection、模型、评测器版本和 RAGAS 覆盖率。
- 任何检索改动都必须回归 `test_3`、`test_5`、`test_8`。

### 生产切换门禁

- 不把 `watsonx_docsqa_colab_v2` 直接切成生产库。
- 只有真实 `rag_chunks` 离线重建为 `rag_chunks_v2`、通过完整门禁并保留回滚快照后，才讨论生产切换。
- Backend 和 Data Worker 切换时必须使用同一 `RAG_COLLECTION`，避免读新写旧。

## 六、暂不做

- 暂不重构现有 React 前端，不以旧前端验收合同产品。
- 暂不把用户合同写入公共法律/案例 Qdrant Collection。
- 暂不接入律师公众号、来源不明的转载文章或付费访问内容。
- 暂不提供地方性法律判断、自动签署建议、诉讼代理或责任担保。
- 暂不把 RAGAS Judge 分数当作法律正确率。

## 七、运行与协作规则

1. 普通后端源码变更优先 `docker-compose ... restart backend`，不要为小改动重建 Embedding 镜像。
2. 依赖或 Dockerfile 变更才重建对应镜像；RAGAS 依赖放在 `evaluation/requirements.txt`。
3. 代码、Compose、评测 summary 和本计划冲突时，优先核对当前源码和实际运行产物。
4. 任何提交前检查 `.env`、真实合同、模型权重、`data/`、数据库持久化目录和 CodeGraph 索引没有被加入 Git。
