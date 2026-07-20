# 容器化避坑指南

> 文档属性：多阶段开发历史记录，不是当前架构清单。Infinity、Redis、旧流式前端等章节
> 用于解释过去为何迁移或淘汰相关方案；当前服务、版本和运行命令以根目录 `README.md`、
> `PLAN.md`、`docs/retrieval-v2-migration.md` 和 `docs/api/` 为准。

> 记录 AI Assistant 六服务容器化过程中踩过的所有坑，按排查顺序排列。
> 每坑含：症状、根因、修复、教训。

---

## 一、基础设施层

### 1. PostgreSQL 数据目录版本冲突

**症状**：`db_pg` 反复重启，日志 `database files are incompatible with server`，PG14 数据被 PG17 镜像读取。

**根因**：旧 `docker-compose.yaml` 用 `ankane/pgvector`（PG14），新镜像 `pgvector/pgvector:pg17`。

**修复**：`rm -rf ./postgres_data`，让 PG17 重建。

**教训**：PostgreSQL 数据目录与主版本绑定。升级镜像前必须清数据或 pg_dump 迁移。

### 2. Docker Hub 间歇性超时

**症状**：`docker pull` 报 `i/o timeout`，有时能拉有时不能。

**根因**：国内到 Docker Hub 直连不稳定。

**修复**：为 Docker daemon 配 systemd 代理 drop-in：
```
/etc/systemd/system/docker.service.d/http-proxy.conf
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:1080"
Environment="HTTPS_PROXY=http://127.0.0.1:1080"
```
然后 `systemctl daemon-reload && systemctl restart docker`。

**教训**：Docker daemon 的代理必须通过 systemd 注入，`HTTP_PROXY` 环境变量对 `docker pull` 无效。

### 3. Docker 基础镜像没有国内镜像

**症状**：`docker.io/library/python:3.12-slim` 等基础镜像拉取慢。

**根因**：国内 Docker Hub 镜像（阿里云、中科大等）已全部停止服务。

**修复**：无法替换，只能靠代理加速。首次拉取后 Docker 缓存，后续构建不重拉。

**教训**：不要尝试 `registry.cn-hangzhou.aliyuncs.com` 等前缀，它们已不可用。

---

## 二、Infinity 向量数据库层

### 4. nightly 镜像与 SDK 版本不匹配

**症状**：backend 启动报 `Expected version index: 0.7.0.dev6, connecting version: 0.6.15`

**根因**：`requirements.txt` 中 `infinity-sdk>=0.6` 装的是 0.6.15，但 `infiniflow/infinity:nightly` 服务端是 0.7.0.dev6。

**修复**：锁定 `infinity-sdk==0.7.0.dev6`。

**教训**：Infinity 服务端和客户端版本必须精确匹配，语义版本不兼容。

### 5. SDK 0.7 dev 版不在清华镜像

**症状**：Docker build 时 `uv pip install --pre infinity-sdk>=0.7.0.dev6` 找不到包。

**根因**：清华 PyPI 镜像只同步正式版，dev/pre-release 不承载。

**修复**：在宿主机用代理下载 wheel 到 `backend/wheels/`，Dockerfile 中离线安装：
```dockerfile
COPY wheels/ ./wheels/
RUN uv pip install --no-cache --system ./wheels/infinity_sdk-0.7.0.dev6-py3-none-any.whl
```

**教训**：dev 版包不能依赖镜像源，必须本地缓存。

### 6. SDK 0.7 列类型格式大改

**症状**：backend 启动报 `Unknown datatype: varchar[]`、`IndexError: list index out of range`

**根因**：SDK 0.7 改了列定义字符串格式。

**修复**：`infinity.py` 中全部更新：

| 0.6.x | 0.7.x |
|-------|-------|
| `"varchar[]"` | `"array,varchar"` |
| `"sparse,30000,float"` | `"sparse,30000,float,int32"` |

**教训**：升 SDK 大版本时，先读 `remote_thrift/utils.py` 的 `get_data_type_from_column_big_info()` 确认格式。

### 7. SDK 0.7 IndexType 枚举重命名

**症状**：`AttributeError: type object 'IndexType' has no attribute 'HNSW'`

**根因**：枚举值改了命名风格。

**修复**：

| 0.6.x | 0.7.x |
|-------|-------|
| `IndexType.HNSW` | `IndexType.Hnsw` |
| `IndexType.Sparse` | `IndexType.BMP` |

**教训**：同上——升 SDK 后跑一遍 `dir(IndexType)` 确认枚举值。

### 8. BMP 索引不接受 metric 参数

**症状**：`Invalid index parameter type: metric`

**根因**：`IndexType.BMP` 的 `create_index` 不接受 `{"metric": "inner_product"}`。

**修复**：`params={}`。

**教训**：不同索引类型允许的参数不同，不能照搬旧参数。

### 9. jieba 分词器在 nightly 中不可用

**症状**：`Analyzer jieba isn't found`

**根因**：nightly 构建没带 jieba 分词库。

**修复**：降级为 `params={"analyzer": "standard"}`。

**教训**：nightly 构建的功能是裁剪的，不要假设全功能可用。

### 10. Infinity 无 HTTP 健康端点

**症状**：`curl http://localhost:23817/health` 返回 `Empty reply from server`，docker-compose healthcheck 一直失败。

**根因**：Infinity 使用 Thrift 二进制协议，不是 HTTP REST。没有 `/health` 端点。

**修复**：移除健康检查，backend 依赖改为 `condition: service_started`。

**教训**：不是所有服务都有 HTTP 健康端点，先 `curl` 验证再写 healthcheck。

### 11. nightly 镜像不稳定频繁重启

**症状**：`db_infinity` 只活了 5 秒就重启，backend 启动时恰好连不上→崩溃。

**根因**：nightly 是每日构建，质量不可控。

**修复**：换为固定版本 `infiniflow/infinity:v0.7.0-dev5`。

**教训**：生产环境锁死版本号，永远不用 `latest`/`nightly`。

---

## 三、Embedding 服务层

### 12. embedding_service 容器缺 curl

**症状**：healthcheck `curl -f http://localhost:8001/health` 失败。

**根因**：`python:3.12-slim` 极简镜像不带 curl。

**修复**：Dockerfile 中 `apt-get install curl`。

**教训**：slim 镜像只有 Python 运行时，需要什么工具自己装。

### 13. backend 本地加载 bge-m3 → OOM

**症状**：backend 启动时尝试 `EmbeddingService(model_path=...)` 加载 2.2GB 模型，容器内存只有 512MB。

**根因**：错误地将 embedding_service 的模型加载逻辑复制到了 backend。

**修复**：替换为 `_HTTPEmbeddingClient`，通过 HTTP 调 `embedding_service:8001/embed`。

**教训**：每个容器只做一件事。backend 不跑模型，embedding_service 不跑业务。

### 14. PyTorch CPU 轮子必须直链下载

**症状**：`--extra-index-url https://mirrors.aliyun.com/pytorch-wheels/cpu/` 找不到 `torch==2.5.1+cpu`（阿里云目录无 PEP 503 索引）。

**根因**：阿里云只存了 `.whl` 文件，没有生成 PEP 503 要求的 `/torch/` 索引页。

**修复**：改为精确文件 URL 直链：
```dockerfile
RUN uv pip install --no-cache --system \
    https://mirrors.aliyun.com/pytorch-wheels/cpu/torch-2.6.0+cpu-cp312-cp312-linux_x86_64.whl \
    fastapi uvicorn[standard] FlagEmbedding
```

**教训**：`--extra-index-url` 要求 PEP 503 兼容的仓库。如果只是个文件目录，用直接 URL。

### 15. torch 版本太低被 transformers 拒绝

**症状**：`ValueError: we now require users to upgrade torch to at least v2.6`

**根因**：最初装了 `torch==2.5.1+cpu`，transformers 因 CVE-2025-32434 强制 ≥2.6。

**修复**：换 `torch-2.6.0+cpu-cp312-cp312-linux_x86_64.whl`。

**教训**：AI 生态链版本联动敏感，transformers/torch/FlagEmbedding 之间有隐含约束。

---

## 四、Backend 启动链路层

### 16. 启动时序：Infinity 未就绪 backend 就崩溃

**症状**：backend `Could not connect to any of [('172.19.0.5', 23817)]`

**根因**：backend 启动时 Infinity 容器存在但 Thrift 服务内部还未初始化完。`depends_on: service_started` 只保证容器进程启动，不保证服务就绪。

**修复**：未根本解决。当前依赖 docker-compose 重试机制（`restart: always`）+ 运气。

**教训**：对无健康检查的服务，后端应加重试逻辑而非指望 `depends_on`。

### 17. chat 路由被注释

**症状**：前端 404 → 502，`POST /api/chat/stream` 路由不存在。

**根因**：`main.py` 中 `create_chat_router` 的 import 和挂载全是注释。

**修复**：在 lifespan 中真正导入路由并 include_router。

**教训**：骨架代码的 TODO 注释要立刻补上，否则忘了就是 404。

### 18. PostgresSaver setup() 被注释

**症状**：`NotImplementedError` in `aget_tuple()`。

**根因**：`chat_graph.py` 中 `checkpointer.setup()` 被注释，导致 checkpoint 表未创建。同时用的是同步版 `PostgresSaver`，LangGraph 的 `AsyncPregelLoop` 调用 `aget_tuple()` 时报 `NotImplementedError`。

**修复**：
1. 取消注释 `setup()` 调用
2. 换用 `AsyncPostgresSaver`

**教训**：PostgresSaver = 同步，AsyncPostgresSaver = 异步。LangGraph 运行时用异步循环，必须异步版。

### 19. setup() 的 CREATE INDEX CONCURRENTLY 需 autocommit

**症状**：`ActiveSqlTransaction: CREATE INDEX CONCURRENTLY cannot run inside a transaction block`

**根因**：`ConnectionPool` 的默认连接 `autocommit=False`。`PostgresSaver.setup()` 内部执行 `CREATE INDEX CONCURRENTLY`，该语句不能在事务中运行。

**修复**：用 `PostgresSaver.from_conn_string(pg_dsn)` 创建临时 autocommit 连接单独执行 `setup()`，再用 `AsyncPostgresSaver(pool)` 编译图。

**教训**：DDL 操作（尤其 CONCURRENTLY）需要 autocommit。连接池默认在事务中。

### 20. AsyncPostgresSaver 只接受 AsyncConnectionPool

**症状**：`Invalid connection type: <class 'psycopg_pool.pool.ConnectionPool'>`

**根因**：`langgraph/checkpoint/postgres/_ainternal.py` 只接受 `AsyncConnection` 或 `AsyncConnectionPool`，不接受同步版 `ConnectionPool`。

**修复**：`main.py` 中改为 `from psycopg_pool import AsyncConnectionPool`。

**教训**：异步版 Checkpointer 要求异步版连接池，类型必须严格匹配。

---

## 五、构建层

### 21. Docker build 时 PyPI 直连超时

**症状**：Dockerfile 中 `uv pip install` 阶段卡死或超时。

**根因**：Docker build 容器内不走宿主机代理，`pypi.org` 直连超时。

**修复**：所有 pip 源改为清华镜像（`UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`），torch 走阿里云直链。

**教训**：Dockerfile 中每一条 `RUN pip install` 都要显式指定国内源，不依赖默认 PyPI。

### 22. ghcr.io 镜像拉取

**症状**：`COPY --from=ghcr.io/astral-sh/uv:latest` 可能拉取慢。

**根因**：GitHub Container Registry 在国外。

**修复**：未替换——uv 二进制只有 25MB，实测 3 秒拉完，不值得折腾。

**教训**：不是所有国外源都要换，体积小的可接受。

---

## 五、配置层

### 23. .env 变量间接引用

**症状**：`ANTHROPIC_AUTH_TOKEN=${KEY}` 可能不被 docker-compose 展开。

**根因**：`.env` 中的 `${KEY}` 依赖 docker-compose 的递归变量展开，行为不稳定。

**修复**：改为直接写值：`ANTHROPIC_AUTH_TOKEN="sk-xxx"`。

**教训**：`.env` 中不要链式引用变量，docker-compose 的展开行为与 shell 不同。

---

## 六、认证系统层

### 24. passlib 与新版本 bcrypt 不兼容

**症状**：`AttributeError: module 'bcrypt' has no attribute '__about__'`，随后 `ValueError: password cannot be longer than 72 bytes`。

**根因**：`passlib` 依赖 bcrypt 的 `__about__.__version__` 属性，但 bcrypt 4.1+ 移除了此属性。

**修复**：不用 passlib，直接用 bcrypt：
```python
import bcrypt

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def _verify_password(password: str, hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), hash.encode())
```

**教训**：passlib 多年未更新，新项目直接用 bcrypt + python-jose，不要引入 passlib 中间层。

### 25. psycopg 异步游标返回 tuple 非 dict

**症状**：`_verify_password(password, row[1])` 把 `username` 当作 `password_hash` 传入 bcrypt，返回 `Invalid salt`。

**根因**：psycopg 的 `AsyncConnection.cursor()` 默认 `row_factory` 是 tuple，不是 dict。`SELECT user_id, username, password_hash` 的 `row[1]` 是 username。

**修复**：`row[2]` 才是 password_hash。或用 `conn.cursor(row_factory=dict_row)` 按列名取值。

**教训**：tuple 索引是隐式约定，列顺序一改就炸。始终验证索引对应的列名，或直接用 `row_factory=dict_row`。

### 26. UUID 对象不能 JSON 序列化

**症状**：JWT 签发时 `TypeError: Object of type UUID is not JSON serializable`。

**根因**：psycopg 读取 PG 的 `UUID` 列返回 Python `uuid.UUID` 对象，直接传给 `json.dumps()` 报错。

**修复**：`str(row["user_id"])` 转换为字符串。

**教训**：数据库驱动返回的 Decimal/UUID/datetime 都不是 JSON 原语，序列化前必须显式转换。

### 27. nginx upstream IP 缓存

**症状**：backend 容器重建后，nginx 持续返回 502，但 `curl http://backend:8000` 在 nginx 容器内能通。

**根因**：nginx 在启动时解析 `backend` 容器的 IP 并缓存。backend 重建后 IP 变了，nginx 仍指向旧 IP。

**修复**：`docker-compose restart frontend` 强制 nginx 重解析 DNS。

**教训**：`proxy_pass http://backend:8000;` 中 nginx 默认在启动时一次性解析 DNS。生产环境应设 `resolver` + `set $backend "backend:8000"` 变量方式强制每次请求时解析。

### 28. 密码强度前后端双重校验

**症状**：前端只传 `password`，后端 Pydantic 的 `confirm_password` 校验拿不到值。

**根因**：`RegisterForm` 中的 `register(username, password)` 只传了 password，但后端 `RegisterRequest` 要求 `confirm_password` 字段（两个独立校验器）。

**修复**：前端 `register(username, password)` 调用时补充 `confirm_password: password`；后端 `confirm_password` 用 `field_validator` 而非独立字段。

**教训**：前后端校验逻辑要对齐——Pydantic 的 validator 依赖字段存在，前端调用时不能漏。

### 29. 注册成功后页面不跳转

**症状**：填写注册表单 → 点击注册 → API 返回成功（200 + token）→ 但页面停在注册表单，未进入聊天界面。

**根因**：`RegisterForm` 中 `await register()` 成功后仅依赖 React context 的 `setToken` 触发整树重渲染。React 19 并发特性下状态更新可能延迟一个调度周期，用户看到表单短暂"卡住"。

**修复**：`register()` 成功后立即调 `onSwitchToLogin()` 切到登录页；随后 auth context 更新到位，自动跳入聊天页。

**教训**：依赖 context 状态驱动的页面跳转时，始终在触发端加一个同步的 UI 过渡。

### 30. 切换密码哈希算法需清空旧用户

**症状**：Argon2id 替换 bcrypt 后，旧用户登录报 `The hash could not be verified`。

**根因**：两种哈希格式完全不兼容——bcrypt 以 `$2b$` 开头，Argon2 以 `$argon2id$` 开头。旧 bcrypt 哈希输给 Argon2 验证器无法解析。

**修复**：清空现有用户数据重建。

**教训**：切换哈希算法 = 旧哈希全部失效。生产环境需提供密码重置流程或迁移脚本。

### 31. Argon2id 参数需匹配 CPU 规模

**症状**：在 2 核服务器上默认参数 `t=3, m=122880` 单次哈希耗时 2-3 秒。

**根因**：argon2-cffi 默认参数面向 4+ 核服务器。

**修复**：针对 2 核 CPU 调优 `time_cost=2, memory_cost=65536 (64MB), parallelism=1, type=Type.ID`。

**教训**：密码哈希参数不是越高越好——需和服务负载、CPU 核数、同机其他进程内存占用权衡。

### 32. localStorage 脏数据导致 React 白屏

**症状**：更新前端部署后浏览器打开只有纯白页面，无任何内容。curl 确认 HTML/JS/CSS 全部 200。

**根因**：多次构建/注册过程中 localStorage 残留了旧格式或损坏的 `user` JSON。`AuthProvider` 启动时 `JSON.parse(savedUser)` 抛异常，React 生产模式下无错误边界 → 整棵树卸载 → `#root` 空。

**修复**：两处防御：
1. `useEffect` 中 try-catch 包裹 localStorage 恢复逻辑，损坏时清除重来
2. 添加 `ErrorBoundary` 类组件包裹 `<AppInner />`，任何渲染崩溃都显示友好提示而非白屏

**教训**：前端但凡有 `JSON.parse(localStorage.getItem(...))`，必须加 try-catch。生产环境必须有一个顶层 ErrorBoundary。

### 33. crypto.randomUUID() 仅 HTTPS/localhost 可用

**症状**：公网 HTTP 访问时页面白屏，控制台报 `crypto.randomUUID is not a function`。

**根因**：`crypto.randomUUID()` 是 Web Crypto API，浏览器的安全上下文要求 HTTPS 或 localhost。通过公网 IP HTTP 访问时该函数不存在。

**修复**：加降级：
```ts
function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}
```

**教训**：`crypto.randomUUID()` 在 HTTP 环境下不可用，所有面向非 HTTPS 环境的前端代码都要加 fallback 或直接用 `uuid` 库。

### 34. useChatStream 切换 session 不重置消息

**症状**：点击"新对话"按钮，消息区域仍显示旧对话内容，而非空白。

**根因**：`useChatStream` 内部 `useState` 存消息——`sessionId` 参数变了，但 hook 内部的 `useState` 不自动重置。React 不知道 state 和 props 之间的关系。

**修复**：加 `useEffect` 监听 `sessionId` 变化，手动 `setMessages([])` + `setIsStreaming(false)`。

**教训**：自定义 hook 接受可变 key 作为参数时，必须显式处理 key 变化时的状态重置。React 不会自动推断这种关联。

### 35. 对话列表纯内存存储刷新即丢

**症状**：刷新页面后左侧对话列表变空，所有历史对话不可恢复。

**根因**：`useState([])` 只在内存中，不持久化。

**修复**：`localStorage` 存取对话列表（最多 50 条），启动时恢复。未来需接后端 `GET /api/chat/sessions` 接口实现真正的跨设备持久化。

**教训**：任何用户期望"还在"的数据都不能只存 React state——至少 fallback 到 localStorage，最终走后端 API。


## 速查表

| 症状关键词 | 直接跳到 |
|-----------|---------|
| `database files are incompatible` | 第 1 条 |
| `i/o timeout` docker pull | 第 2 条 |
| `Expected version index` | 第 4 条 |
| `Unknown datatype: varchar[]` | 第 6 条 |
| `IndexType has no attribute 'HNSW'` | 第 7 条 |
| `Invalid index parameter type: metric` | 第 8 条 |
| `Analyzer jieba isn't found` | 第 9 条 |
| `Empty reply from server` (Infinity) | 第 10 条 |
| Infinity 反复重启 | 第 11 条 |
| embedding_service unhealthy | 第 12 条 |
| backend OOM | 第 13 条 |
| `torch==2.5.1+cpu` not found | 第 14 条 |
| `upgrade torch to at least v2.6` | 第 15 条 |
| `Could not connect to any of` | 第 16 条 |
| `POST /api/chat/stream 404` | 第 17 条 |
| `NotImplementedError` aget_tuple | 第 18 条 |
| `CREATE INDEX CONCURRENTLY` | 第 19 条 |
| `Invalid connection type: ConnectionPool` | 第 20 条 |
| Docker build pip 超时 | 第 21 条 |
| `Invalid salt` | 第 25 条 |
| `UUID is not JSON serializable` | 第 26 条 |
| backend 重建后始终 502 | 第 27 条 |
| `password cannot be longer than 72 bytes` | 第 24 条 |
| passlib / bcrypt 不兼容 | 第 24 条 |
| `.env` 变量取不到值 | 第 23 条 |
