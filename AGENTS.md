## Agent skills

### Issue tracker

本项目的任务和 PRD 记录在公开 GitHub 仓库 `ccxx777/-agent` 的 Issues 中。参见 `docs/agents/issue-tracker.md`。

### Domain docs

本项目采用 single-context（单一领域上下文）文档布局。参见 `docs/agents/domain.md`。

## 发布后服务器同步约定（项目记忆）

每次开发变更完成后，统一按以下顺序操作：

1. 在本地完成测试、提交，并将当前 `main` 同时推送到 GitHub（`github`）和 Gitee（`contract-agent`）。两个远端都成功后，才能进入服务器同步；任一推送失败都先停止，不在服务器上拉取不完整版本。
2. 通过 `devbox` SSH 登录部署服务器（`root@106.15.63.71`，密钥文件为本机 `~/.ssh/langgraph.pem`），进入 `/root/my-ai-research`。
3. 先检查服务器工作区是否干净以及远端指向：`git status --short`、`git remote -v`。发现未提交的服务器本地改动时，不覆盖、不重置，先报告并确认处理方式。
4. 确认远端后使用 `git pull --ff-only <已确认的远端> main`，禁止无检查地执行可能产生合并提交的普通 `git pull`。
5. 根据本次变更范围，只重启或重建受影响的服务；不要因为普通后端代码或文档改动重建 embedding 镜像。具体决策表和健康检查命令见 [`docs/agents/server-sync.md`](docs/agents/server-sync.md)。
6. 部署后记录服务器实际 commit、服务状态和 `/health` 检查结果；若验证失败，停止继续操作并保留日志。
