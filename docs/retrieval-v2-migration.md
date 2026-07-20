# 通用知识库Retrieval v2迁移与切换

## 目标

本次版本同时解决三个已验证问题：

1. 生成Prompt仍绑定华中科技大学，造成通用数据集上的错误拒答和幻觉；
2. BGE-M3 Sparse保存在Payload中，查询时滚动扫描整个Collection；
3. Query Specificity只识别中文旧停用词，英文自然问句始终得到 `S=0.8`。

迁移遵循“旧库只读 → 新库离线构建 → 独立基线 → 配置切换 → 可立即回滚”。

## Qdrant能力边界

- 原生Sparse Vector Index自Qdrant v1.7.0起可用，并且是精确倒排式检索。
- Payload Full-text Index用于匹配和过滤，本身不产生BM25排序分数。
- BM25排序由独立命名Sparse Vector提供。
- 当前Qdrant `multilingual` tokenizer基于Charabia；日文另用Vaporetto，不是jieba-rs。
- 中文Payload全文索引优先探测 `multilingual`；若当前服务器拒绝该配置，迁移器自动改用
  Jieba预分词字段和 `word` tokenizer。

官方资料：

- [Sparse Vector Index](https://qdrant.tech/documentation/manage-data/indexing/#sparse-vector-index)
- [Full-text Index Tokenizers](https://qdrant.tech/documentation/manage-data/indexing/#full-text-index)
- [Full-text Search与BM25](https://qdrant.tech/documentation/search/text-search/full-text-search/)

## v2 Collection结构

| 名称 | 类型 | 作用 |
|---|---|---|
| `dense` | Named Dense Vector | BGE-M3语义召回 |
| `bge_m3_sparse` | Named Sparse Vector | BGE-M3词法权重召回 |
| `bm25_word` | Named Sparse Vector | 英文word分词BM25排序 |
| `bm25_zh` | Named Sparse Vector | 中文Jieba分词BM25排序 |

Payload同时保留：

- `chunk_text`：答案上下文和Reranker输入；
- `fulltext_en`：英文 `word` Full-text Index；
- `fulltext_zh`：Qdrant `multilingual` Full-text Index；
- `fulltext_zh_segmented`：Jieba预分词回退字段；
- `retrieval_schema_version=2`。

默认不再把 `sparse_indices` 和 `sparse_values` 复制到目标Payload。旧Collection仍完整
保留，因此不需要为回滚重复保存一份Payload Sparse。

## Prompt变化

新Prompt：

- 身份改为通用知识库助手；
- 使用与用户问题相同的语言回答；
- 只使用Context，不允许用模型记忆补充；
- 全部Context都无有效证据时才允许拒答；
- 部分证据可回答时，输出有证据的部分并说明边界；
- 每个事实性句子都要引用；
- 禁止与问题无关的扩展。

检索工具名称从 `search_hust_rules` 改为 `search_knowledge_base`。

## Query Specificity v2

统一映射：

```text
S = clamp(0.8 - 0.6 × signal_density, 0.2, 0.8)
semantic_weight = 1 - S
literal_weight = S
```

英文：

- 空格/标点word分词，不做词形还原；
- 使用从NLTK stopwords二次筛选出的结构功能词；
- 排除否定和程度词；
- `What is the learning rate?` 的密度为3/5，`S=0.44`。

中文：

- 使用Jieba词性分词；
- 仅统计 `r/u/p/c/y`；
- 动词和名词不计入社交填充词；
- 额外用固定社交套话白名单补偿词性漏判。

语言检测：

- `langdetect` 置信度低于0.7或文本不超过3字符时，直接使用 `S=0.5`；
- `zh/zh-cn/zh-tw`统一为中文；
- 其他语言统一走英文逻辑；
- 日志记录语言、置信度、密度、S和最终权重。

## 第一步：服务器版本预检

更新代码后，在仓库根目录执行：

```bash
uv run \
  --with-requirements evaluation/requirements.txt \
  python evaluation/qdrant_v2_collection_migrate.py preflight \
  --qdrant-url http://127.0.0.1:6333 \
  --source watsonx_docsqa_colab_v1 \
  --expected-points 6759
```

记录输出中的 `qdrant_version`，并在 `.env` 中固定当前镜像版本，例如：

```text
QDRANT_IMAGE=qdrant/qdrant:v1.x.y
```

不要在迁移过程中升级或降级Qdrant存储卷。原生Sparse Index最低需要1.7；本方案还使用动态IDF，因此服务器版本低于1.10时停止迁移。

## 第二步：离线构建新Collection

```bash
uv run \
  --with-requirements evaluation/requirements.txt \
  python evaluation/qdrant_v2_collection_migrate.py migrate \
  --qdrant-url http://127.0.0.1:6333 \
  --source watsonx_docsqa_colab_v1 \
  --target watsonx_docsqa_colab_v2 \
  --expected-points 6759 \
  --state data/benchmarks/watsonxDocsQA/prepared/qdrant-v2-migration-state.json \
  --manifest data/benchmarks/watsonxDocsQA/prepared/qdrant-v2-migration-manifest.json
```

中断后确认来源和目标名称没有变化，再添加 `--resume`。脚本会重新幂等Upsert，不会删除
旧库或覆盖未知目标库。

## 第三步：只重建一次Backend

Prompt、语言检测和Jieba依赖进入Backend，因此这一步需要一次计划内重建：

```bash
docker-compose \
  -f docker-compose.yaml \
  -f docker-compose.dev.yaml \
  up -d --build backend
```

Embedding和Qdrant镜像不需要重建。

## 第四步：v2独立检索基线

```bash
docker exec backend python \
  /app/evaluation/watsonx_docsqa_retrieval_baseline.py \
  --questions /app/data/benchmarks/watsonxDocsQA/prepared/test.jsonl \
  --output /app/data/benchmarks/watsonxDocsQA/results/retrieval_baseline_v2 \
  --collection watsonx_docsqa_colab_v2
```

比较v1/v2并强制Hit@3不退化：

```bash
uv run python evaluation/compare_retrieval_baselines.py \
  --old data/benchmarks/watsonxDocsQA/results/retrieval_baseline_v1/summary.json \
  --new data/benchmarks/watsonxDocsQA/results/retrieval_baseline_v2/summary.json \
  --output data/benchmarks/watsonxDocsQA/results/retrieval_v1_v2_comparison.json
```

切换门：

- 30/30完成且零错误；
- v2 Hit@3不低于v1的93.33%；
- 对 `test_3`、`test_5` 和新增差异题做人工检查；
- 平均/P95延迟明显下降，确认日志中不再出现Sparse Payload扫描回退。

## 第五步：生成与RAGAS回归

使用独立输出目录，避免与v1断点混用：

```bash
uv run python evaluation/watsonx_docsqa_full_baseline.py prepare \
  --collection watsonx_docsqa_colab_v2 \
  --generations data/benchmarks/watsonxDocsQA/results/generation_baseline_v2/details.jsonl \
  --review-output data/benchmarks/watsonxDocsQA/results/generation_baseline_v2/review \
  --container-generation-output /app/data/benchmarks/watsonxDocsQA/results/generation_baseline_v2
```

人工确认后使用相同v2路径执行 `approve` 和 `score`。重点比较：

- 六道错误拒答是否消失；
- Faithfulness最低题是否减少幻觉；
- AnswerCorrectness是否提高；
- 缺少引用数量是否下降。

## 生产切换和回滚

生产知识库需要另建 `rag_chunks_v2`，通过同样的迁移和基线门后，在 `.env` 设置：

```text
RAG_COLLECTION=rag_chunks_v2
```

只重建Backend容器配置，不修改或删除旧库。出现异常时把环境变量改回：

```text
RAG_COLLECTION=rag_chunks
```

再重启Backend即可回滚。旧Collection至少保留一个完整观察周期。
