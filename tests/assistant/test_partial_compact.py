"""Tests for preserved tail compaction (partial compact)."""

import pytest

from openlist_ani.assistant.core.models import (
    Message,
    ProviderResponse,
    Role,
)
from openlist_ani.assistant.memory.compactor import AutoCompactor

from .conftest import MockProvider


class TestPartialCompact:
    """Tests for AutoCompactor.partial_compact() — preserved tail compaction."""

    @pytest.mark.asyncio
    async def test_basic_partial_compact(self):
        """Should summarize head and preserve tail."""
        provider = MockProvider(
            [
                # The compaction LLM call
                ProviderResponse(
                    text="<summary>Earlier conversation was about weather.</summary>"
                ),
            ]
        )
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="You are a helpful assistant."),
            Message(role=Role.USER, content="What's the weather?"),
            Message(role=Role.ASSISTANT, content="It's sunny today."),
            Message(role=Role.USER, content="What about tomorrow?"),
            Message(role=Role.ASSISTANT, content="Rain expected."),
            Message(role=Role.USER, content="Thanks, what should I wear?"),
            Message(role=Role.ASSISTANT, content="Bring an umbrella."),
        ]

        # Compact with pivot at index 3 (keep messages from index 3 onward)
        result = await compactor.partial_compact(messages, pivot_index=3)

        assert result is not None
        # System message should be preserved
        assert result[0].role == Role.SYSTEM
        assert result[0].content == "You are a helpful assistant."
        # Summary should be injected as user message
        assert any(
            "summary" in m.content.lower() or "weather" in m.content.lower()
            for m in result
            if m.role == Role.USER
        )
        # Tail messages should be preserved
        tail_contents = [m.content for m in result if m.role != Role.SYSTEM]
        assert "What about tomorrow?" in tail_contents or any(
            "What about tomorrow?" in c for c in tail_contents
        )

    @pytest.mark.asyncio
    async def test_auto_pivot_preserves_tail(self):
        """When no pivot_index given, should auto-preserve N tail messages."""
        provider = MockProvider(
            [
                ProviderResponse(text="<summary>Old conversation summary.</summary>"),
            ]
        )
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="System"),
            Message(role=Role.USER, content="Message 1"),
            Message(role=Role.ASSISTANT, content="Response 1"),
            Message(role=Role.USER, content="Message 2"),
            Message(role=Role.ASSISTANT, content="Response 2"),
            Message(role=Role.USER, content="Message 3"),
            Message(role=Role.ASSISTANT, content="Response 3"),
        ]

        # Default preserved_tail=2 should keep last 2 non-system messages
        result = await compactor.partial_compact(messages, preserved_tail=2)

        assert result is not None
        # Should have system + summary + preserved tail
        # Last 2 messages should be preserved
        result_contents = [m.content for m in result]
        assert "Message 3" in result_contents
        assert "Response 3" in result_contents

    @pytest.mark.asyncio
    async def test_too_few_messages(self):
        """Should return None if too few messages."""
        provider = MockProvider()
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="System"),
            Message(role=Role.USER, content="Hello"),
        ]

        result = await compactor.partial_compact(messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_pivot_index(self):
        """Should return None for invalid pivot indices."""
        provider = MockProvider()
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="System"),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi"),
        ]

        # Pivot at 0 means nothing to keep
        assert await compactor.partial_compact(messages, pivot_index=0) is None
        # Pivot beyond messages
        assert await compactor.partial_compact(messages, pivot_index=10) is None

    @pytest.mark.asyncio
    async def test_compaction_failure_returns_none(self):
        """Should return None if LLM call fails."""

        class FailingProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
                raise RuntimeError("Provider down")

        provider = FailingProvider()
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="System"),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi"),
            Message(role=Role.USER, content="How are you?"),
            Message(role=Role.ASSISTANT, content="Good"),
        ]

        result = await compactor.partial_compact(messages, pivot_index=3)
        assert result is None

    @pytest.mark.asyncio
    async def test_system_only_head(self):
        """Should return None if head only contains system message."""
        provider = MockProvider()
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="System"),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi"),
        ]

        # pivot_index=1 means head = [system], tail = [user, assistant]
        result = await compactor.partial_compact(messages, pivot_index=1)
        assert result is None
