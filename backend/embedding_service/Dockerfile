FROM python:3.12-slim

# 1. 安装 uv 并配置清华源
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_HTTP_TIMEOUT=300
ENV UV_TCP_KEEPALIVE=1
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 软件源加速 + 安装构建环境
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    python3-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 3. 核心依赖（不再安装 FlagEmbedding，用 vendored _bge_m3.py）
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --system \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    https://mirrors.aliyun.com/pytorch-wheels/cpu/torch-2.6.0+cpu-cp312-cp312-linux_x86_64.whl \
    fastapi \
    uvicorn \
    "transformers==4.43.3"

COPY _bge_m3.py .
COPY app.py .

ENV PYTHONUNBUFFERED=1
ENV BGE_M3_MODEL_PATH=/app/models/bge-m3
ENV EMBEDDING_CPU_THREADS=4
ENV EMBEDDING_MAX_CONCURRENT=2

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
