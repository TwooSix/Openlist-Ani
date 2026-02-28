from abc import ABC, abstractmethod
from typing import Any

from openai import AsyncOpenAI

from ..constants import LLM_REQUEST_TIMEOUT


class LLMClient(ABC):
    @abstractmethod
    async def complete_chat(
        self, messages: list[dict[str, str]], model: str | None = None
    ) -> str: ...


class OpenAILLMClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = LLM_REQUEST_TIMEOUT,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model

    async def complete_chat(
        self, messages: list[dict[str, str]], model: str | None = None
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
        }
        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
