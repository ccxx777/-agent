"""AI Assistant Backend 应用包。

依赖方向为 ``api → services → infrastructure/components``，Agent 由 ``main``
装配后作为 Chat/Session Service 的执行引擎。任何模块都不应反向导入 API 层。
"""
