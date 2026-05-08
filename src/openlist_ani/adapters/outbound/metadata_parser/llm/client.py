from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..constants import LLM_REQUEST_TIMEOUT
from ..settings import MetadataParserSettings

# Default max_tokens for Anthropic messages API.
_ANTHROPIC_DEFAULT_MAX_TOKENS = 8192


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
        from openai import AsyncOpenAI

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


class AnthropicLLMClient(LLMClient):
    """LLM client using the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = LLM_REQUEST_TIMEOUT,
    ) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=api_key, base_url=base_url, timeout=timeout
        )
        self._model = model

    async def complete_chat(
        self, messages: list[dict[str, str]], model: str | None = None
    ) -> str:
        # Anthropic requires system messages as a separate parameter.
        system_parts: list[str] = []
        api_messages: list[dict[str, str]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg["content"])
            else:
                api_messages.append(msg)

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": api_messages,
            "max_tokens": _ANTHROPIC_DEFAULT_MAX_TOKENS,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        response = await self._client.messages.create(**kwargs)
        # Filter out ThinkingBlock; only extract TextBlock content.
        for block in response.content:
            if block.type == "text":
                return block.text
        return ""


def create_llm_client(settings: MetadataParserSettings) -> LLMClient:
    """Create an LLM client for the configured metadata parser provider."""
    match settings.provider_type:
        case "openai":
            return OpenAILLMClient(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
            )
        case "anthropic":
            return AnthropicLLMClient(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
            )
        case _:
            raise ValueError(
                f"Unknown provider_type: '{settings.provider_type}'. "
                "Supported values: 'openai', 'anthropic'."
            )
