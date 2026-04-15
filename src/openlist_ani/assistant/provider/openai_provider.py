"""
OpenAI-compatible LLM provider.

Handles message format conversion and tool_calls parsing for
OpenAI API (and compatible providers like DeepSeek, vLLM, etc.).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from openlist_ani.assistant._constants import (
    DEFAULT_TEMPERATURE,
    MODEL_MAX_OUTPUT_TOKENS,
    PROVIDER_MAX_RETRIES,
    PROVIDER_TIMEOUT_SECONDS,
)
from openlist_ani.assistant.core.models import (
    Message,
    ProviderResponse,
    Role,
    ToolCall,
)
from openlist_ani.assistant.tool.base import BaseTool

from .base import Provider

from loguru import logger


def _get_max_tokens_for_model(model: str) -> int:
    """Get model-specific default max_tokens."""
    # Direct match
    if model in MODEL_MAX_OUTPUT_TOKENS:
        return MODEL_MAX_OUTPUT_TOKENS[model][0]

    # Partial match (e.g., "gpt-4o-2024-05-13" matches "gpt-4o")
    for model_prefix, (default_tokens, _) in MODEL_MAX_OUTPUT_TOKENS.items():
        if model_prefix in model or model.startswith(model_prefix):
            return default_tokens

    from openlist_ani.assistant._constants import DEFAULT_MAX_OUTPUT_TOKENS
    return DEFAULT_MAX_OUTPUT_TOKENS


class OpenAIProvider(Provider):
    """Provider implementation using the OpenAI SDK.

    Configured with:
    - SDK-level timeout (600s)
    - SDK-level max retries
    - Dynamic max_tokens based on model
    - Temperature defaults to 1.0
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = PROVIDER_TIMEOUT_SECONDS,
        max_retries: int = PROVIDER_MAX_RETRIES,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._model = model
        self._default_max_tokens = _get_max_tokens_for_model(model)

    def get_default_max_tokens(self) -> int:
        """Return model-specific default max_tokens."""
        return self._default_max_tokens

    async def close(self) -> None:
        """Close the underlying httpx connection pool."""
        await self._client.close()

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> ProviderResponse:
        api_messages = self._convert_messages(messages)
        max_tokens = max_tokens_override or self._default_max_tokens
        temp = temperature if temperature is not None else DEFAULT_TEMPERATURE

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temp,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        # Parse tool_calls if present
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=arguments,
                    )
                )

        return ProviderResponse(
            text=msg.content or "",
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    @staticmethod
    def _accumulate_tool_call_deltas(
        collected: dict[int, dict[str, str]], tool_call_deltas: list[Any]
    ) -> None:
        """Accumulate tool call deltas from a single chunk into the collector."""
        for tc_delta in tool_call_deltas:
            idx = tc_delta.index
            func = tc_delta.function
            func_name = getattr(func, "name", None) if func else None
            func_args = getattr(func, "arguments", None) if func else None

            entry = collected.setdefault(
                idx, {"id": "", "name": "", "args": ""},
            )
            if tc_delta.id:
                entry["id"] = tc_delta.id
            if func_name:
                entry["name"] = func_name
            if func_args:
                entry["args"] += func_args

    @staticmethod
    def _build_tool_calls_from_collected(
        collected: dict[int, dict[str, str]],
    ) -> list[ToolCall]:
        """Parse collected tool call data into a list of ToolCall objects."""
        tool_calls: list[ToolCall] = []
        for tc_data in collected.values():
            try:
                arguments = json.loads(tc_data["args"] or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=arguments,
                )
            )
        return tool_calls

    def _build_stream_kwargs(
        self,
        api_messages: list[dict],
        max_tokens: int,
        temperature: float | None,
        tools: list[dict] | None,
    ) -> dict[str, Any]:
        """Build kwargs dict for the OpenAI streaming API call."""
        temp = temperature if temperature is not None else DEFAULT_TEMPERATURE
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temp,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[ProviderResponse, None]:
        """Stream an OpenAI chat completion.

        Yields partial ProviderResponse objects:
        - Text deltas as they arrive
        - A final response with tool_calls, stop_reason, and usage
        """
        api_messages = self._convert_messages(messages)
        max_tokens = max_tokens_override or self._default_max_tokens
        kwargs = self._build_stream_kwargs(
            api_messages, max_tokens, temperature, tools,
        )

        stream = await self._client.chat.completions.create(**kwargs)

        collected_tool_calls: dict[int, dict[str, str]] = {}
        usage_data: dict[str, int] = {}

        async for chunk in stream:
            if chunk.usage:
                usage_data = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta and delta.content:
                yield ProviderResponse(text=delta.content)

            if delta and delta.tool_calls:
                self._accumulate_tool_call_deltas(
                    collected_tool_calls, delta.tool_calls
                )

            if choice.finish_reason:
                yield ProviderResponse(
                    text="",
                    tool_calls=self._build_tool_calls_from_collected(
                        collected_tool_calls
                    ),
                    stop_reason=choice.finish_reason,
                    usage=usage_data,
                )

    def format_tool_definitions(self, tools: list[BaseTool]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def format_raw_tools(self, tools: list[dict]) -> list[dict]:
        """Convert neutral tool dicts to OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        """Convert internal Message format to OpenAI API message format."""
        api_messages: list[dict] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                api_messages.append({"role": "system", "content": msg.content})

            elif msg.role == Role.USER:
                api_messages.append({"role": "user", "content": msg.content})

            elif msg.role == Role.ASSISTANT:
                entry: dict[str, Any] = {"role": "assistant"}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                    # OpenAI requires content to be present (can be null)
                    entry["content"] = msg.content or None
                else:
                    entry["content"] = msg.content
                api_messages.append(entry)

            elif msg.role == Role.TOOL:
                for result in msg.tool_results:
                    api_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result.tool_call_id,
                            "content": result.content,
                        }
                    )

        return api_messages
