# AI 知识库助手 — 当前实施计划

> 更新日期：2026-07-20
> 当前阶段：Retrieval v2 隔离评测。前端继续冻结，生产 `rag_chunks` 暂不切换。

## 一、目标与边界

当前目标不是继续堆功能，而是用固定数据集证明以下改造是否真实有效：

1. 通用 Prompt 能否减少错误拒答和无依据扩展；
2. Qdrant 原生 Sparse/BM25 能否降低检索延迟；
3. 双语 Query Specificity 能否修复英文问题长期 `S=0.8` 的偏差；
4. 在性能改善的同时，Hit@3、Faithfulness 和答案正确性不能退化。

## 二、架构清单

### 保留

- FastAPI + LangGraph Agent 与 PostgreSQL Checkpointer。
- BGE-M3 独立 Embedding 服务。
- 自研 L1 → L2 → L3 Cascade Funnel。
- Dense 强语义保底和 Reranker 失败降级。
- 独立 Data Worker、SHA-256 指纹和增量文件监听。
- 稳定 `RetrievalPayload`、Eval API 与分阶段评测工作流。

### 已完成的合并与收口

- Agent、Eval API 和离线评测统一通过 `RetrievalService` 使用生产召回链。
- 召回输出统一为 `context + contexts + documents`。
- Collection 通过 `RAG_COLLECTION` / 构造参数注入，不再依赖固定模块常量。
- 文档入库统一由 `data_worker/ingest/service.py` 编排。
- 认证、会话和检索模型分别收口到 `schemas/` 与 `services/`。

### 已退出生产路径

- Component Registry 与动态自动发现。
- YAML Pipeline。
- Backend 内旧 Sentinel。
- Redis。
- 重复的 `messages`、`token_usage` 旧表。
- 旧同步 RRF `run()`：源码暂留作演化记录，但 Agent 不调用。

## 三、当前数据流

```text
POST /api/chat 或 POST /api/eval/rag_query
  → LangGraph
  → search_knowledge_base
  → RetrievalService
      ├─ BGE-M3 Query Dense/Sparse
      ├─ 中英文语言检测与 Query Specificity
      └─ 指定 Qdrant Collection
  → L1：Dense + Native BGE Sparse + BM25 并发 Top-10
  → L2：分路 Min-Max + 动态权重 + Dense 语义保底 → Top-10
  → L3：Qwen3 Reranker → Top-3
  → RetrievalPayload
      ├─ context：带引用的生成 Prompt
      ├─ contexts：实际生成上下文
      └─ documents：召回元数据与最终 Rank
  → 通用知识库 Prompt 生成答案
  → PostgreSQL Checkpoint
```

旧 `rag_chunks` 仍允许 Payload Sparse 回退；v2 Collection 必须走原生 Sparse Index，
正常日志中不应出现 `Payload扫描回退`。

## 四、已经完成

- [x] RAG 主链稳定返回 `answer + contexts + documents`。
- [x] watsonxDocsQA 1144 文档、45 训练题、30 测试题完成标准化。
- [x] 6759 个 BGE-M3 预计算 Chunk 导入隔离 Collection。
- [x] v1 30 题检索基线：Hit@3 93.33%，零运行错误。
- [x] v1 30 题生成、人工抽查与 RAGAS：三个指标覆盖率均为 100%。
- [x] Prompt 改为通用知识库并加强引用、部分回答和拒答边界。
- [x] 中英文 Query Specificity 按信号词密度计算，S 截断为 `[0.2, 0.8]`。
- [x] Qdrant 1.9.0 → 1.9.7 → 1.10.1 分阶段升级并完成快照校验。
- [x] `watsonx_docsqa_colab_v2` 原生 Sparse/BM25 离线迁移：6759/6759。
- [x] v2 3 题检索 Smoke：3/3 完成，零错误。

## 五、正在进行

- [ ] 完成 `watsonx_docsqa_colab_v2` 的 30 题检索基线。
- [ ] 运行 `compare_retrieval_baselines.py`，要求 Hit@3 不低于 v1 的 93.33%。
- [ ] 比较 Hit@1、MRR@3、逐题退化、Mean/P50/P95 和零命中题。
- [ ] 确认原生 Sparse 路径不再执行全库 Payload Scroll。

## 六、门禁通过后的顺序

1. 运行 v2 的 30 题答案生成基线。
2. 重新生成重点样本抽查报告，不沿用 v1 人工确认。
3. 检查原来的 6 道错误拒答和 Faithfulness 低分题。
4. 人工确认后运行 v2 完整 30 题 RAGAS。
5. 对比 Answer Correctness、Faithfulness、Context Relevance 与覆盖率。
6. 只有检索和生成均通过，才为生产数据离线构建 `rag_chunks_v2`。

## 七、明确不做

- 暂不修改 React 前端。
- 不直接覆盖或删除旧 Qdrant Collection。
- 不根据 3 题 Smoke 或单道失败题调整 Top-K、阈值和融合权重。
- 不把 RAGAS 安装进生产 Backend 镜像。
- 不因为 Backend 修改重建 Embedding 镜像。
- 不把 LLM Judge 分数当作唯一结论，必须保留严格 Gold 指标和人工审查。

## 八、验收标准

### Retrieval v2

- 30/30 完成且零错误。
- Hit@3 ≥ 93.33%。
- 新增零命中题为 0；若存在，必须逐题解释。
- Mean/P95 延迟相对 v1 有可重复的改善。
- Collection 为 6759 Points，命名向量和 IDF 配置可被 Client 正确解析。

### Generation v2

- 30/30 完成，无结构性失败。
- 错误拒答数量下降。
- 缺少引用和无效引用不增加。
- Faithfulness、Answer Correctness 不低于 v1，Context Relevance 保持稳定。
- 所有指标同时报告覆盖率、运行签名和重点样本。

### 生产切换

- 使用新名称离线建库并保留旧库至少一个观察周期。
- 通过 `RAG_COLLECTION` 切换；回滚只需恢复旧值并重启 Backend。
- Data Worker 与 Backend 指向同一生产 Collection 后才允许恢复增量写入。
- 当前 `data_worker/docker-compose.yml` 尚未显式转发 `RAG_COLLECTION`；生产切换前必须补齐并验证，不能只修改Backend。
