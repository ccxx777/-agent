# 发布后服务器同步运行手册

本文档是项目级发布记忆的详细版本，说明本地推送成功后如何安全同步服务器，以及如何按改动范围选择最小的重启或重建动作。

## 适用范围

- 本地工作区：`F:\\claudecode\\my-ai-research-master`
- 服务器：`devbox`，`root@106.15.63.71:22`
- 服务器项目目录：`/root/my-ai-research`
- SSH 私钥：本机 `~/.ssh/langgraph.pem`（只引用路径，不将私钥提交到仓库）
- 发布分支：`main`
- 发布远端：GitHub 远端名 `github`，Gitee 远端名 `contract-agent`

服务器上的实际远端名称可能因历史部署不同而不同。因此，拉取前必须查看 `git remote -v`，再选择指向已推送仓库的远端，不能假定服务器上的 `origin` 一定正确。

## 标准发布顺序

### 1. 本地验证和推送

```powershell
git status --short
git diff --check
git push github main
git push contract-agent main
```

只有两条 `git push` 都成功，才允许进入服务器步骤。推送失败时，不要在服务器执行 `git pull`。

### 2. SSH 后检查服务器工作区

```bash
ssh devbox
cd /root/my-ai-research
git status --short
git remote -v
git branch --show-current
git rev-parse --short HEAD
```

如果 `git status --short` 有输出，说明服务器存在未提交改动。此时停止，不执行覆盖、重置或强制拉取；先保存或确认这些改动的处置方式。

### 3. 快进拉取

确认远端 URL 指向本次已成功推送的 GitHub 或 Gitee 仓库后，使用显式远端和 `--ff-only`：

```bash
git pull --ff-only <已确认的远端> main
git rev-parse --short HEAD
```

`--ff-only` 用于防止服务器悄悄产生合并提交。拉取后的 commit 应与本地刚推送的 commit 一致；不一致时停止部署并检查分支、远端和工作区。

## 按改动范围选择最小操作

项目使用 Docker Compose v2 的 `docker-compose` 命令。以下示例都在 `/root/my-ai-research` 执行，并保留开发覆盖文件。

| 本次变更 | 推荐动作 | 说明 |
| --- | --- | --- |
| README、文档、evaluation 脚本或普通 backend Python 代码 | `docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml restart backend` | 容器仍在运行时不需要重新构建镜像；若容器不存在，改用 `up -d backend`。 |
| backend/requirements、backend/Dockerfile 或 backend 构建配置 | 先 `build backend`，再 `up -d backend` | 只重建 backend，避免牵连 embedding。 |
| frontend 源码、依赖或 Dockerfile | 先 `build frontend`，再 `up -d frontend` | 只重建 frontend。 |
| embedding_service 源码、依赖、模型或 Dockerfile | 先 `build embedding_service`，再 `up -d embedding_service` | 该服务重建可能耗时较长，仅在确有相关变更时执行。 |
| data_worker 源码或依赖 | 按其 Compose 文件只重建/重启 `sentinel` | 先用 `docker-compose config --services` 确认服务名和 Compose 文件。 |
| Compose 文件或环境变量 | `up -d <受影响服务>`，必要时加 `--build` | 先检查最终配置，不要无差别重建全部服务。 |
| 新增数据库迁移 | 先核对迁移文件和备份，再按项目迁移命令执行 | 不因为普通代码更新重复执行旧迁移。 |

不要对普通后端代码、前端页面或文档改动执行全量 `build`。服务器磁盘空间有限，当前根分区曾接近 90% 使用率；清理 Docker 资源或删除数据属于单独的高风险操作，必须先确认目标和获得明确授权。

## 部署后检查

```bash
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml ps
curl --fail --silent --show-error http://127.0.0.1:8000/health
curl --fail --silent --show-error http://127.0.0.1:8001/health
docker logs --tail 100 backend
```

如果修改了 Data Worker，还要检查 `sentinel` 的状态和最近日志；如果修改了数据库迁移，还要核对迁移结果。任何健康检查失败时，保留 `docker logs` 输出，不继续扩大重建范围。

## 一次性快速模板

下面是下一次发布时的最小人工流程。`<已确认的远端>` 必须根据服务器的 `git remote -v` 替换，不能直接照抄：

```bash
# 本地：两个推送都成功后才继续
git push github main
git push contract-agent main

# 服务器
ssh devbox
cd /root/my-ai-research
git status --short
git remote -v
git pull --ff-only <已确认的远端> main

# 按变更范围执行最小重启/重建
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml restart backend

# 验证
docker-compose -f docker-compose.yaml -f docker-compose.dev.yaml ps
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

若本次不是 backend 变更，应将最后的 `restart backend` 替换为上方决策表中的对应动作；文档-only 变更不需要重启服务。
