"""
Abstract base class for LLM providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openlist_ani.assistant.core.models import Message, ProviderResponse
    from openlist_ani.assistant.tool.base import BaseTool


class Provider(ABC):
    """Abstract LLM provider interface.

    Supports:
    - temperature (default 1.0)
    - max_tokens (dynamic, model-specific)
    - SDK timeout/retry configuration
    """

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> ProviderResponse:
        """Send messages to the model and get a response.

        Args:
            messages: Conversation history.
            tools: Tool definitions in provider-specific format.
            max_tokens_override: Override the default max_tokens for this call.
                Used for max_output_tokens escalation recovery.
            temperature: Override the default temperature for this call.

        Returns:
            Unified ProviderResponse.
        """
        ...

    @abstractmethod
    def format_tool_definitions(self, tools: list[BaseTool]) -> list[dict]:
        """Convert BaseTool instances to provider-specific tool definition format.

        Args:
            tools: List of tool instances.

        Returns:
            List of tool definition dicts for the provider API.
        """
        ...

    def format_raw_tools(self, tools: list[dict]) -> list[dict]:
        """Convert neutral tool dicts to provider-specific format.

        Each dict must contain ``name``, ``description``, and ``parameters``.
        Subclasses override this to wrap in their API's envelope format.

        The default implementation returns the dicts unchanged, which suits
        providers that accept ``{name, description, input_schema}`` directly
        (e.g. Anthropic).

        Args:
            tools: Neutral tool definitions.

        Returns:
            Provider-formatted tool definitions.
        """
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[ProviderResponse, None]:
        """Stream a chat completion response.

        Yields partial ProviderResponse objects as the model generates:
        - Text deltas: ``ProviderResponse(text="chunk")``
        - Final: ``ProviderResponse(tool_calls=[...], stop_reason="stop", usage={...})``

        Default implementation falls back to non-streaming ``chat_completion()``.

        Args:
            messages: Conversation history.
            tools: Tool definitions in provider-specific format.
            max_tokens_override: Override the default max_tokens for this call.
            temperature: Override the default temperature for this call.

        Yields:
            Partial ProviderResponse objects.
        """
        response = await self.chat_completion(
            messages, tools, max_tokens_override, temperature
        )
        yield response

    async def close(self) -> None:
        """Close underlying HTTP clients and release resources.

        Subclasses should override to close their specific SDK clients.
        Default implementation is a no-op.
        """

    def get_default_max_tokens(self) -> int:
        """Get the default max_tokens for this provider's model.

        Subclasses should override based on model name.
        """
        from openlist_ani.assistant._constants import DEFAULT_MAX_OUTPUT_TOKENS

        return DEFAULT_MAX_OUTPUT_TOKENS
