# Qdrant — 向量数据库

| 容器名 | 端口 | 镜像 | 协议 |
|--------|------|------|------|
| `db_qdrant` | `6333` (REST), `6334` (gRPC) | `qdrant/qdrant:latest` | HTTP REST |

## 连接

```python
from qdrant_client import QdrantClient

# 容器内
client = QdrantClient(url="http://db_qdrant:6333")

# 宿主机
client = QdrantClient(url="http://127.0.0.1:6333")
```

```bash
# REST API 测试
curl http://127.0.0.1:6333/collections
curl -X POST http://127.0.0.1:6333/collections/rag_chunks/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{"limit": 1, "with_payload": true}'
```

## Collection 结构

```
集合: rag_chunks
  向量: 1024-dim float, Cosine distance
  HNSW: M=16, ef_construct=100, full_scan_threshold=10000
  Payload (每个 point):
    doc_id:            str        (文档 UUID)
    chunk_id:          str        (分块 UUID)
    chunk_text:        str        (分块正文)
    title:             str        (文档标题)
    source:            str        (文件路径 / SHA256)
    sha256:            str        (文件指纹)
    sparse_indices:    list[int]  (BGE-M3 token IDs)
    sparse_values:     list[float](BGE-M3 token weights)
    user_id:           str        (上传者)
```

## 连通性验证

```bash
# 集合列表
curl -s http://127.0.0.1:6333/collections | python3 -m json.tool

# 点数统计
curl -s http://127.0.0.1:6333/collections/rag_chunks | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['result']['points_count'])"
```

## 迁移历史

从 Infinity 迁移至 Qdrant 的原因：

| 问题 | Infinity | Qdrant |
|------|----------|--------|
| 2 核 CPU 稳定性 | nightly segfault 频繁 | 运行稳定 |
| 健康检查 | Thrift 无 HTTP 端点 | REST API 可 curl |
| SDK 兼容性 | 0.6/0.7 版本断裂 | 1.9.x 稳定 |
| 检索延迟 | 普遍偏高 | HNSW + Cosine 更优 |

## 已知问题

- 镜像不含 `curl`，docker-compose 健康检查改用 `service_started`
- 不使用 Qdrant 官方 healthcheck，依赖容器启动成功即可
- Sparse 检索需全量 scroll（Qdrant 无原生稀疏索引），通过暴力内积计算，可通过 `RECALL_POOL_SIZE` 控制上限
