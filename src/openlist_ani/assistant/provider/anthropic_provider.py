"""
Anthropic LLM provider.

Handles message format conversion and tool_use block parsing for
the Anthropic Messages API.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from anthropic import AsyncAnthropic

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

    # Partial match
    for model_prefix, (default_tokens, _) in MODEL_MAX_OUTPUT_TOKENS.items():
        if model_prefix in model or model.startswith(model_prefix):
            return default_tokens

    from openlist_ani.assistant._constants import DEFAULT_MAX_OUTPUT_TOKENS

    return DEFAULT_MAX_OUTPUT_TOKENS


class AnthropicProvider(Provider):
    """Provider implementation using the Anthropic SDK.

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
        self._client = AsyncAnthropic(
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
        system_prompt, api_messages = self._convert_messages(messages)
        max_tokens = max_tokens_override or self._default_max_tokens
        kwargs = self._build_request_kwargs(
            api_messages, max_tokens, temperature, system_prompt, tools
        )

        response = await self._client.messages.create(**kwargs)
        text_parts, tool_calls, thinking_blocks = self._parse_response_content(
            response.content
        )

        return ProviderResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            },
            thinking_blocks=thinking_blocks,
        )

    def _build_request_kwargs(
        self,
        api_messages: list[dict],
        max_tokens: int,
        temperature: float | None,
        system_prompt: str,
        tools: list[dict] | None,
    ) -> dict[str, Any]:
        """Build kwargs dict for Anthropic message requests."""
        temp = temperature if temperature is not None else DEFAULT_TEMPERATURE
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temp,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools
        return kwargs

    @staticmethod
    def _thinking_block_to_dict(block: Any) -> dict:
        """Convert an Anthropic thinking block to a pass-back-safe dict."""
        if hasattr(block, "model_dump"):
            return block.model_dump()
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "signature": getattr(block, "signature", ""),
        }

    @staticmethod
    def _tool_call_from_block(block: Any) -> ToolCall:
        """Convert an Anthropic tool_use block to a ToolCall."""
        return ToolCall(
            id=block.id,
            name=block.name,
            arguments=block.input if isinstance(block.input, dict) else {},
        )

    @classmethod
    def _parse_response_content(
        cls,
        content: list[Any],
    ) -> tuple[list[str], list[ToolCall], list[dict]]:
        """Parse Anthropic content blocks into normalized response fields."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        thinking_blocks: list[dict] = []

        for block in content:
            if block.type == "thinking":
                thinking_blocks.append(cls._thinking_block_to_dict(block))
            elif block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(cls._tool_call_from_block(block))
        return text_parts, tool_calls, thinking_blocks

    @staticmethod
    def _handle_block_start(
        event: Any,
        current_block_idx: int,
        current_tool_blocks: dict[int, dict[str, str]],
        current_thinking_blocks: dict[int, dict[str, str]],
    ) -> int:
        """Handle a content_block_start event. Returns updated block index."""
        current_block_idx += 1
        block = event.content_block
        if block.type == "tool_use":
            current_tool_blocks[current_block_idx] = {
                "id": block.id,
                "name": block.name,
                "input_json": "",
            }
        elif block.type == "thinking":
            current_thinking_blocks[current_block_idx] = {
                "thinking": getattr(block, "thinking", ""),
                "signature": getattr(block, "signature", ""),
            }
        return current_block_idx

    @staticmethod
    def _handle_block_delta(
        event: Any,
        current_block_idx: int,
        current_tool_blocks: dict[int, dict[str, str]],
        current_thinking_blocks: dict[int, dict[str, str]],
    ) -> ProviderResponse | None:
        """Handle a content_block_delta event. Returns a text response or None."""
        delta = event.delta
        if delta.type == "text_delta":
            return ProviderResponse(text=delta.text)
        if (
            delta.type == "input_json_delta"
            and current_block_idx in current_tool_blocks
        ):
            current_tool_blocks[current_block_idx]["input_json"] += delta.partial_json
        if (
            delta.type == "thinking_delta"
            and current_block_idx in current_thinking_blocks
        ):
            current_thinking_blocks[current_block_idx]["thinking"] += getattr(
                delta, "thinking", ""
            )
        if (
            delta.type == "signature_delta"
            and current_block_idx in current_thinking_blocks
        ):
            current_thinking_blocks[current_block_idx]["signature"] = getattr(
                delta, "signature", ""
            )
        return None

    @staticmethod
    def _extract_tool_calls_from_message(final: Any) -> list[ToolCall]:
        """Extract ToolCall list from a final Anthropic message."""
        tool_calls: list[ToolCall] = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            tool_calls.append(AnthropicProvider._tool_call_from_block(block))
        return tool_calls

    @staticmethod
    def _extract_thinking_blocks_from_message(final: Any) -> list[dict]:
        """Extract thinking blocks from a final Anthropic message."""
        thinking_blocks: list[dict] = []
        for block in final.content:
            if block.type == "thinking":
                thinking_blocks.append(AnthropicProvider._thinking_block_to_dict(block))
        return thinking_blocks

    @staticmethod
    def _build_thinking_blocks_from_tracked(
        tracked: dict[int, dict[str, str]],
    ) -> list[dict]:
        """Build thinking block dicts from stream-tracked data."""
        blocks: list[dict] = []
        for data in tracked.values():
            block: dict[str, str] = {
                "type": "thinking",
                "thinking": data["thinking"],
            }
            if data.get("signature"):
                block["signature"] = data["signature"]
            blocks.append(block)
        return blocks

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[ProviderResponse, None]:
        """Stream an Anthropic chat completion.

        Yields partial ProviderResponse objects:
        - Text deltas as they arrive
        - A final response with tool_calls, stop_reason, and usage
        """
        system_prompt, api_messages = self._convert_messages(messages)
        max_tokens = max_tokens_override or self._default_max_tokens
        temp = temperature if temperature is not None else DEFAULT_TEMPERATURE

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temp,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            current_tool_blocks: dict[int, dict[str, str]] = {}
            current_thinking_blocks: dict[int, dict[str, str]] = {}
            current_block_idx = -1

            async for event in stream:
                if event.type == "content_block_start":
                    current_block_idx = self._handle_block_start(
                        event,
                        current_block_idx,
                        current_tool_blocks,
                        current_thinking_blocks,
                    )
                elif event.type == "content_block_delta":
                    response = self._handle_block_delta(
                        event,
                        current_block_idx,
                        current_tool_blocks,
                        current_thinking_blocks,
                    )
                    if response is not None:
                        yield response

            final = await stream.get_final_message()

            # Prefer thinking blocks from final message (most complete),
            # fall back to stream-tracked blocks if final message lacks them
            thinking_blocks = self._extract_thinking_blocks_from_message(final)
            if not thinking_blocks and current_thinking_blocks:
                thinking_blocks = self._build_thinking_blocks_from_tracked(
                    current_thinking_blocks
                )
                logger.debug(
                    f"Used stream-tracked thinking blocks "
                    f"({len(thinking_blocks)} blocks)"
                )

            yield ProviderResponse(
                text="",
                tool_calls=self._extract_tool_calls_from_message(final),
                stop_reason=final.stop_reason or "",
                usage={
                    "prompt_tokens": final.usage.input_tokens,
                    "completion_tokens": final.usage.output_tokens,
                },
                thinking_blocks=thinking_blocks,
            )

    def format_tool_definitions(self, tools: list[BaseTool]) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    @staticmethod
    def _convert_assistant_message(msg: Message) -> dict | None:
        """Convert an assistant Message to an Anthropic API message dict."""
        content_blocks: list[dict[str, Any]] = []
        # Thinking blocks must come first and be passed back as-is
        for tb in msg.thinking_blocks:
            content_blocks.append(tb)
        if msg.content:
            content_blocks.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                }
            )
        if content_blocks:
            return {"role": "assistant", "content": content_blocks}
        return None

    @staticmethod
    def _convert_tool_message(msg: Message) -> dict | None:
        """Convert a tool Message to an Anthropic API message dict."""
        result_blocks: list[dict[str, Any]] = [
            {
                "type": "tool_result",
                "tool_use_id": result.tool_call_id,
                "content": result.content,
                "is_error": result.is_error,
            }
            for result in msg.tool_results
        ]
        if result_blocks:
            return {"role": "user", "content": result_blocks}
        return None

    def _convert_messages(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """Convert internal Message format to Anthropic API format.

        Anthropic requires system messages to be passed separately.
        Returns (system_prompt, api_messages).
        """
        system_parts: list[str] = []
        api_messages: list[dict] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(msg.content)
            elif msg.role == Role.USER:
                api_messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                entry = self._convert_assistant_message(msg)
                if entry is not None:
                    api_messages.append(entry)
            elif msg.role == Role.TOOL:
                entry = self._convert_tool_message(msg)
                if entry is not None:
                    api_messages.append(entry)

        return "\n\n".join(system_parts), api_messages
