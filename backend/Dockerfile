FROM python:3.12-slim

# 1. 安装 uv 并配置清华源镜像
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_HTTP_TIMEOUT=300
ENV UV_TCP_KEEPALIVE=1
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV UV_EXTRA_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cpu/
ENV UV_COMPILE_BYTECODE=1

# 2. PostgreSQL 客户端的最小编译环境
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev libpq-dev

WORKDIR /app

# 3. 依赖层独立于源码；只改 app/ 时可完整命中缓存
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --system -r requirements.txt

# 4. 最后复制应用代码
COPY app/ ./app/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
