"""可选的远程 DeepSeek OCR Provider。

默认关闭。开启后，扫描页原始图片会发送到配置的 OpenAI-compatible OCR
服务；服务层会把 ``external_raw_image_sent`` 写入隐私报告，提醒后续产品
隐私策略不能把“文字已脱敏”误认为“原始图片未离开服务器”。
"""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx

logger = logging.getLogger(__name__)


class ContractOCRUnavailable(RuntimeError):
    """OCR 没有配置或远程调用失败。"""


class ContractOCRClient:
    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.enabled = enabled and bool(api_key)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    async def extract(self, image_bytes: bytes) -> str:
        if not self.enabled:
            raise ContractOCRUnavailable("合同 OCR 未配置")

        image_data = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请将这页合同准确转换为纯文本，保留原有段落和条款顺序，不要添加解释。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                        },
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        endpoint = f"{self.base_url}/chat/completions"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(endpoint, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(
                        item.get("text", "") for item in content if isinstance(item, dict)
                    )
                text = str(content).strip()
                if not text:
                    raise ContractOCRUnavailable("OCR 返回空文本")
                return text
            except (httpx.HTTPError, KeyError, TypeError, ValueError, ContractOCRUnavailable) as error:
                last_error = error
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(2**attempt)

        logger.warning("Contract OCR failed after %s attempts", self.max_retries)
        raise ContractOCRUnavailable("OCR 服务暂时不可用") from last_error

