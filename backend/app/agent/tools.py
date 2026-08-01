"""LangGraph 可调用工具定义。

工具层只把 Agent 的字符串调用协议转换成业务 Service 调用。实际向量化、
Cascade Funnel 和结果适配都由 ``RetrievalService`` 完成，因此这里没有任何
召回权重或排序逻辑。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


def create_retrieval_tools(
    retrieval_service: RetrievalService,
    legal_retrieval_service: Any | None = None,
) -> list[BaseTool]:
    """创建通用和法律检索工具；两条服务边界使用不同 Collection。"""

    @tool
    async def search_knowledge_base(query: str) -> str:
        """搜索当前通用知识库中的事实、制度、产品、流程和专业资料。"""
        logger.info("进入工具: search_knowledge_base (query=%.60s)", query)
        result = await retrieval_service.retrieve(query)
        return result.model_dump_json()

    tools: list[BaseTool] = [search_knowledge_base]
    if legal_retrieval_service is not None:

        @tool
        async def search_legal_knowledge_base(query: str) -> str:
            """搜索已治理的中国大陆劳动法 A/B 级法律资料。"""

            logger.info("进入工具: search_legal_knowledge_base (query=%.60s)", query)
            result = await legal_retrieval_service.retrieve(query)
            return result.model_dump_json()

        tools.append(search_legal_knowledge_base)
    return tools
