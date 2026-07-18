"""Qdrant 连接信息与客户端工厂。

Backend 的自研召回器目前接收 URL，而其他维护代码可能需要原生客户端。
``QdrantGateway`` 同时提供这两种访问方式，但不实现任何召回或排序逻辑。
"""

from __future__ import annotations

from qdrant_client import QdrantClient


class QdrantGateway:
    """保存 Qdrant 连接地址并按需创建同步客户端。"""

    def __init__(self, url: str) -> None:
        self.url = url

    def create_client(self) -> QdrantClient:
        """创建原生客户端；调用方负责控制其使用范围。"""
        return QdrantClient(url=self.url)
