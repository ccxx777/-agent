"""外部基础设施适配层。

该包集中管理 PostgreSQL、Qdrant、Embedding HTTP 服务和 LLM Provider 等
进程外依赖。业务服务只依赖这里暴露的适配对象，不在 API 或 Agent 节点中
直接拼接连接参数。
"""
