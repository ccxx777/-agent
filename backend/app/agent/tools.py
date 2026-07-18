"""LangGraph 可调用工具定义。

工具层只把 Agent 的字符串调用协议转换成业务 Service 调用。实际向量化、
Cascade Funnel 和结果适配都由 ``RetrievalService`` 完成，因此这里没有任何
召回权重或排序逻辑。
"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, tool

from app.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


def create_retrieval_tools(retrieval_service: RetrievalService) -> list[BaseTool]:
    """创建绑定当前 ``RetrievalService`` 的 Agent 工具列表。"""

    @tool
    async def search_hust_rules(query: str) -> str:
        """搜索华中科技大学校内规章制度等官方文档。"""
        logger.info("进入工具: search_hust_rules (query=%.60s)", query)
        result = await retrieval_service.retrieve(query)
        return result.model_dump_json()

    return [search_hust_rules]
