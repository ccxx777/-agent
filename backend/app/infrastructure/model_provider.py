"""生成模型 Provider。

职责：把模型名称、OpenAI-compatible Base URL 和 API Key 组装成 LangChain
ChatModel。Agent 节点只接收已构造的模型，不读取环境变量。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI


class ModelProvider:
    """创建 Backend 主对话模型。"""

    def __init__(self, *, model: str, base_url: str, api_key: str) -> None:
        self._model = model.replace("[1m]", "").strip()
        self._base_url = base_url
        self._api_key = api_key

    def create_chat_model(self) -> ChatOpenAI:
        """返回与原 Agent 相同参数的非流式 ChatOpenAI 实例。"""
        return ChatOpenAI(
            model=self._model,
            base_url=self._base_url,
            api_key=self._api_key,
            streaming=False,
            temperature=0.1,
        )
