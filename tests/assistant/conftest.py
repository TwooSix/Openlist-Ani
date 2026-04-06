"""
Shared fixtures for assistant tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.assistant.core.models import (
    Message,
    ProviderResponse,
)
from openlist_ani.assistant.provider.base import Provider
from openlist_ani.assistant.tool.base import BaseTool


@pytest.fixture(autouse=True)
def _no_sleep():
    """Replace asyncio.sleep with a no-op in assistant loop tests to avoid
    real delays from exponential backoff retries."""
    with patch("openlist_ani.assistant.core.loop.asyncio.sleep", new_callable=AsyncMock):
        yield


class MockProvider(Provider):
    """Mock provider that returns pre-configured responses."""

    def __init__(self, responses: list[ProviderResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0
        self._calls: list[tuple[list[Message], list[dict] | None]] = []

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> ProviderResponse:
        self._calls.append((messages, tools))
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return ProviderResponse(text="Default mock response.")

    def format_tool_definitions(self, tools: list[BaseTool]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]


class ReadOnlyTool(BaseTool):
    """A concurrency-safe mock tool for testing."""

    def __init__(self, name: str = "read_tool", result: str = "read result") -> None:
        self._name = name
        self._result = result
        self.call_count = 0
        self.call_args: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"A concurrency-safe test tool ({self._name})"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return True

    async def execute(self, **kwargs: object) -> str:
        self.call_count += 1
        self.call_args.append(dict(kwargs))
        return self._result


class WriteTool(BaseTool):
    """A non-concurrency-safe mock tool for testing."""

    def __init__(self, name: str = "write_tool", result: str = "write result") -> None:
        self._name = name
        self._result = result
        self.call_count = 0
        self.call_args: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"A write test tool ({self._name})"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return False

    async def execute(self, **kwargs: object) -> str:
        self.call_count += 1
        self.call_args.append(dict(kwargs))
        return self._result


class ErrorTool(BaseTool):
    """A tool that always raises an error."""

    @property
    def name(self) -> str:
        return "error_tool"

    @property
    def description(self) -> str:
        return "A tool that always fails"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> str:
        raise RuntimeError("Tool execution failed!")
