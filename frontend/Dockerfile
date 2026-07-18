FROM node:20-alpine AS builder

WORKDIR /app

# 1. npm 镜像 → 淘宝源
RUN npm config set registry https://registry.npmmirror.com

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci

COPY . .
RUN npm run build

# ── 生产镜像：Nginx ──
FROM nginx:alpine

# 静态文件
COPY --from=builder /app/dist /usr/share/nginx/html

# Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
