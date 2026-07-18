# Frontend — Nginx + React SPA

| 容器名 | 暴露端口 | 镜像 |
|--------|---------|------|
| `frontend` | `3000:80` | `nginx:alpine` |

## 路由规则

### / — React SPA

所有非 `/api/` 请求回退到 `index.html`：

```
location / {
    try_files $uri $uri/ /index.html;
}
```

### /api/* — 反向代理到 backend

```
location /api/ {
    proxy_pass http://backend:8000;
}
```

**通用代理配置**

| 指令 | 值 | 作用 |
|------|-----|------|
| `proxy_set_header` | `Host $host` | 透传原始 Host |
| `proxy_set_header` | `X-Real-IP $remote_addr` | 客户端真实 IP |
| `proxy_read_timeout` | `300s` | 长连接超时 |

## 测试

```bash
# 前端页面
curl -s http://127.0.0.1:3000/ | head -5

# 健康检查（经 nginx → backend）
curl http://127.0.0.1:3000/health

# 对话接口（经 nginx → backend）
curl -X POST http://127.0.0.1:3000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"你好","session_id":"test","user_id":"dev"}'
```

## 已知问题

- 依赖 `backend` 为 `service_started`，不等待 backend 内部 lifespan 完成
- backend 启动慢（~15s）时，前端前几秒请求返回 502
- backend 重启后 IP 变更，Nginx 可能缓存旧 IP，需 `docker-compose restart frontend`
