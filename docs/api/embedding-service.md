# BGE-M3 Embedding Service

| 容器名 | 端口 | 模型 | 并发控制 |
|--------|------|------|---------|
| `embedding_service` | `8001`（内网） | BGE-M3, 2.2GB, CPU 推理 | Semaphore(1) |

## 端点

### GET /health

```bash
curl http://embedding_service:8001/health
```

```json
{"status": "ok", "model_loaded": true}
```

### POST /embed

批量文本向量化（Dense + Sparse）。

```bash
curl -X POST http://embedding_service:8001/embed \
  -H 'Content-Type: application/json' \
  -d '{"texts":["查询一","查询二"],"dense":true,"sparse":true}'
```

| 请求字段 | 类型 | 默认 | 说明 |
|----------|------|------|------|
| `texts` | `list[str]` | 必填 | 待编码文本 |
| `dense` | `bool` | `true` | 是否返回 1024 维稠密向量 |
| `sparse` | `bool` | `true` | 是否返回 token 权重稀疏向量 |

```json
{
  "dense": [[0.12, -0.03, ...], [0.08, 0.11, ...]],
  "sparse": [{"1045":0.32, "2018":0.18}, {"99":0.45}]
}
```

- `dense[i]`：1024 维 float 稠密向量（Cosine 归一化后存入 Qdrant）
- `sparse[i]`：`{token_id: weight}`，token_id < 30000，权重为 BGE-M3 输出的 token importance

## 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `BGE_M3_MODEL_PATH` | `/app/models/bge-m3` | 模型目录（宿主机只读挂载） |
| `EMBEDDING_CPU_THREADS` | `2` | torch 线程数，2 核 CPU 限制 |
| `EMBEDDING_MAX_CONCURRENT` | `1` | Semaphore 并发上限 |

## 调用方注意

- **timeout 设置**：大文件向量化可能超 120 秒，sentinel 使用 `timeout=None` + 小批次 (batch_size=2) 分批次调用
- **并发限制**：Semaphore(1) 保证单任务推理，多并发请求排队等待
- **模型加载**：首次启动约 5 秒，期间 `/embed` 返回 503

## 已知问题

- Docker compose 资源限制：`cpus: 1.5`, `memory: 4G`
- 单次推理约 2 核，峰值受 Docker cpus 限制兜底
- 模型文件宿主机只读挂载：`/root/my-ai-research/models/bge-m3:/app/models/bge-m3:ro`
