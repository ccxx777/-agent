FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_HTTP_TIMEOUT=300
ENV UV_TCP_KEEPALIVE=1
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev libpq-dev

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --system -r requirements.txt

# 代码层最后复制；容器通过 Python package 入口启动。
COPY __init__.py config.py cli.py legal_cli.py watcher.py ./data_worker/
COPY ingest/ ./data_worker/ingest/

ENV PYTHONUNBUFFERED=1
