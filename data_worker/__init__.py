"""独立文档入库 Worker。

该包与 Backend 进程解耦，通过 PostgreSQL、Qdrant 和 Embedding HTTP 接口协作。
标准入口是 ``python -m data_worker.cli``。
"""
