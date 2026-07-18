# Backend — AI Agent 引擎

| 容器名 | 端口 | 框架 |
|--------|------|------|
| `backend` | `8000`（内网，不对外暴露） | FastAPI + LangGraph ReAct Agent |

## 端点

### GET /health

直接返回，不经 lifespan。

```bash
curl http://backend:8000/health
```

```json
{"status": "ok"}
```

### POST /api/chat

对话接口（非流式）。通过 frontend Nginx 代理访问。

```bash
curl -X POST http://127.0.0.1:3000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"博士生学习年限是多久","session_id":"s1","user_id":"dev"}'
```

**请求**

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | `str` | 用户问题 |
| `session_id` | `str` | 会话 UUID（同时作为 LangGraph thread_id） |
| `user_id` | `str` | 用户标识（默认 "anonymous"） |

**响应**

```json
{
  "answer": "博士研究生基本学习年限为3至5年 [1]...",
  "session_id": "test-4way-003"
}
```

**Graph 执行链**

```
START → condense_memory → chatbot → tools → generate_answer → END
```

1. `condense_memory` — 对超过窗口的最旧一问一答生成摘要
2. `chatbot` — SystemMessage 注入 + LLM 决定是否调用工具
3. `tools` — `search_hust_rules` 调用 `RetrievalService`
4. `generate_answer` — 解析结构化检索结果并生成最终答案

### GET /api/chat/history/{session_id}

从 PostgresSaver checkpoints 提取历史消息。

```bash
curl http://127.0.0.1:3000/api/chat/history/s1
```

```json
{
  "messages": [
    {"role": "human", "content": "博士生学习年限是多久"},
    {"role": "ai", "content": "博士研究生基本学习年限为3至5年..."}
  ]
}
```

过滤规则：跳过 ToolMessage / SystemMessage，只返回 human 和 ai 角色。

## RAG 检索架构

Cascade Funnel 三层漏斗（详见 PLAN.md 第五章）：

```
L1 Recall:  Dense HNSW + Sparse 内积 + BM25 并发 Top-10 → ID 去重
L2 Coarse:  缺省惩罚动态归一化 + 语义保底 → Top-10
L3 Fine:    硅基流动 Reranker 交叉编码 → Top-3 (异常降级至粗排)
```

冻结的 Cascade Funnel 实现在 `components/retriever/qdrant/v2_0_0/main.py`；
`services/retrieval_service.py` 只负责向量化、调用算法和稳定结果适配。

## 启动流程

```
1. `load_dotenv(override=True)` → 加载运行配置
2. 创建 `psycopg` `AsyncConnectionPool` → `db_pg:5432`
3. 创建 Embedding、Qdrant、Model Provider 基础设施适配器
4. 初始化 Auth、Retrieval、Chat、Session Service
5. 编译 LangGraph → `PostgresSaver`（checkpointer）
6. 挂载 Auth、Chat、Sessions、Eval 路由
7. 日志输出 `Backend ready`
```

## 依赖服务

| 服务 | 条件 | 连接方式 |
|------|------|---------|
| `db_pg` | healthy | psycopg DSN |
| `db_qdrant` | started | HTTP REST (:6333) |
| `embedding_service` | healthy | HTTP POST /embed |

## 已知问题

- 启动约 15 秒，期间返回 502
- `db_qdrant` 镜像不含 curl，无法 HTTP 健康检查，设为 `service_started`
- 非流式输出：POST /api/chat 需等待完整生成
- `asyncio.gather` 调用已移除，现为单次 LLM 调用
