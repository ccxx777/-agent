# Issue Tracker：GitHub

本项目的任务和 PRD 记录在公开 GitHub 仓库 `ccxx777/-agent` 的 Issues 中。

所有 GitHub CLI 命令必须显式指定：

`-R ccxx777/-agent`

原因是本地仓库的 `origin` 指向私有 Gitee，不能依赖 `gh` 从当前 Git Remote 自动推断 GitHub 仓库。

## 隐私边界

GitHub 仓库及其 Issues 为公开内容，禁止提交：

- 用户合同原文或片段
- 姓名、身份证号、手机号、地址等个人信息
- API Key、Token、Cookie、`.env` 内容
- 未公开的法律复核材料
- 私有 Gitee 仓库中不准备公开的资料

问题复现应使用脱敏样本或人工构造样本。

## 常用操作

- 创建 Issue：`gh issue create -R ccxx777/-agent --title "..." --body-file <file>`
- 查看 Issue：`gh issue view <number> -R ccxx777/-agent --comments`
- 列出 Issue：`gh issue list -R ccxx777/-agent --state open`
- 评论：`gh issue comment <number> -R ccxx777/-agent --body-file <file>`
- 添加标签：`gh issue edit <number> -R ccxx777/-agent --add-label "..."`
- 关闭 Issue：`gh issue close <number> -R ccxx777/-agent --comment "..."`

多行正文优先保存为临时 Markdown 文件后通过 `--body-file` 提交，避免 PowerShell 多行转义错误。

## Pull Request 是否作为需求入口

否。外部 Pull Request 默认不进入需求分流队列。

## Skill 术语映射

- “发布到 Issue Tracker”：创建 GitHub Issue。
- “读取相关 Ticket”：读取对应 GitHub Issue 及其评论。
- 裸编号 `#42` 可能是 Issue 或 PR，应先确认对象类型。
