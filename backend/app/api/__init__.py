"""FastAPI HTTP 边界。

该包只处理请求校验、依赖注入、HTTP 错误映射和响应序列化。业务流程委托给
``app.services``，避免路由直接操作数据库或拼装 LangGraph。
"""
