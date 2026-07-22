# Qdrant — 向量与词法检索存储

| 容器名 | 端口 | 当前验证镜像 | Python Client |
|---|---|---|---|
| `db_qdrant` | `6333` REST、`6334` gRPC | `qdrant/qdrant:v1.10.1` | `qdrant-client>=1.10,<1.11` |

镜像必须通过 `.env` 的 `QDRANT_IMAGE` 固定，不能继续依赖 `latest` 的本地缓存含义。

## 连接与版本

```python
from qdrant_client import QdrantClient

container_client = QdrantClient(url="http://db_qdrant:6333")
host_client = QdrantClient(url="http://127.0.0.1:6333")
```

```bash
curl http://127.0.0.1:6333/
curl http://127.0.0.1:6333/collections
```

Qdrant Server 1.10 的 Collection 如果包含 `modifier=idf`，Client 1.9 虽然会收到
HTTP 200，但会在反序列化时因未知字段失败。Server 和 Client 必须同时达到 1.10。

## 当前 Collection

| Collection | Points | 用途 | Schema |
|---|---:|---|---|
| `rag_chunks` | 744（2026-07-20快照） | 当前生产知识库 | v1兼容结构 |
| `watsonx_docsqa_v1` | 245 | 早期未完成导入 | v1 |
| `watsonx_docsqa_colab_v1` | 6759 | watsonxDocsQA冻结基线 | v1 |
| `watsonx_docsqa_colab_v2` | 6759 | Retrieval v2隔离评测 | v2 |

点数会随生产入库变化；表中数字是升级时的核验快照，不是永久常量。

## v1兼容结构

```text
unnamed Dense 1024-dim / Cosine
Payload:
  doc_id, chunk_id, chunk_text, title, source, sha256, user_id
  sparse_indices[], sparse_values[]
```

v1的Sparse需要Backend Scroll全部Point后计算内积，仅作为生产切换前的兼容回退。

## v2结构

```text
Named vectors:
  dense             Dense 1024-dim / Cosine
  bge_m3_sparse     Native Sparse Index
  bm25_word         English BM25 + modifier=idf
  bm25_zh           Chinese BM25 + modifier=idf

Payload:
  doc_id, chunk_id, chunk_text, title, source, sha256, user_id
  retrieval_schema_version=2
  fulltext_en, fulltext_zh, fulltext_zh_segmented
```

Qdrant `multilingual` Full-text Index在本次1.10.1环境探测成功。Payload Full-text Index
负责匹配/过滤，BM25排序由命名Sparse Vector完成。

## 精确点数检查

```bash
curl --fail --silent --show-error \
  -X POST \
  -H 'Content-Type: application/json' \
  -d '{"exact":true}' \
  http://127.0.0.1:6333/collections/watsonx_docsqa_colab_v2/points/count
```

## 快照与升级记录

2026-07-20在升级前为三个旧Collection创建并下载Snapshot，核对文件大小和SHA256后，
按以下路径升级：

```text
1.9.0 → 1.9.7 → 1.10.1
```

每一步均检查根接口版本与 `744 / 245 / 6759` 精确点数。不要跳过中间Minor版本，
不要在没有宿主机快照时直接修改存储卷对应镜像。

## v2迁移与切换

迁移器：`evaluation/qdrant_v2_collection_migrate.py`。

它只创建新Collection、幂等Upsert和写入状态/Manifest，不删除旧库。生产切换通过：

```text
RAG_COLLECTION=rag_chunks_v2
```

发生异常时恢复 `RAG_COLLECTION=rag_chunks` 并重启Backend。完整流程见
`docs/retrieval-v2-migration.md`。

独立Data Worker的Compose已显式转发该变量；生产切换前仍需在服务器核验 Backend 与
Sentinel 的环境值一致。评测Collection不允许由Sentinel增量写入。

## 已知边界

- 旧Collection仍会触发Payload Sparse回退，只有v2命名向量才使用原生Sparse。
- Qdrant镜像不含curl，Compose健康检查仍以容器启动为主，应用层用宿主机REST复核。
- 外部Reranker不属于Qdrant，其延迟不能算作向量库内部搜索耗时。
- 生产 `rag_chunks_v2` 尚未创建；评测门禁通过前不得覆盖 `rag_chunks`。
