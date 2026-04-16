from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....config import LLMConfig

from ..constants import LLM_REQUEST_TIMEOUT

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


def create_llm_client(llm_config: LLMConfig) -> LLMClient:
    """Create an LLM client based on provider_type in config.

    Args:
        llm_config: LLM configuration from user config.

    Returns:
        An LLMClient instance for the configured provider.

    Raises:
        ValueError: If provider_type is not recognized.
    """
    match llm_config.provider_type:
        case "openai":
            return OpenAILLMClient(
                api_key=llm_config.openai_api_key,
                base_url=llm_config.openai_base_url,
                model=llm_config.openai_model,
            )
        case "anthropic":
            return AnthropicLLMClient(
                api_key=llm_config.openai_api_key,
                base_url=llm_config.openai_base_url,
                model=llm_config.openai_model,
            )
        case _:
            raise ValueError(
                f"Unknown provider_type: '{llm_config.provider_type}'. "
                "Supported values: 'openai', 'anthropic'."
            )
