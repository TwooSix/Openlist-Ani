"""Tests for resource cleanup — provider close, loop shutdown, dream task tracking."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.provider.base import Provider
from openlist_ani.assistant.provider.openai_provider import OpenAIProvider
from openlist_ani.assistant.provider.anthropic_provider import AnthropicProvider

from .conftest import MockProvider


# ------------------------------------------------------------------ #
# Provider.close()
# ------------------------------------------------------------------ #


class TestProviderClose:
    """Verify that providers expose and call close() correctly."""

    @pytest.mark.asyncio
    async def test_base_provider_close_is_noop(self):
        """Base Provider.close() is a no-op that doesn't raise."""
        provider = MockProvider()
        await provider.close()  # should not raise

    @pytest.mark.asyncio
    async def test_openai_provider_close_delegates(self):
        """OpenAIProvider.close() calls self._client.close()."""
        provider = OpenAIProvider(
            api_key="test", base_url="https://test.example.com", model="gpt-4o",
        )
        provider._client = MagicMock()
        provider._client.close = AsyncMock()

        await provider.close()

        provider._client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_anthropic_provider_close_delegates(self):
        """AnthropicProvider.close() calls self._client.close()."""
        provider = AnthropicProvider(
            api_key="test", base_url="https://test.example.com", model="claude-3-5-sonnet",
        )
        provider._client = MagicMock()
        provider._client.close = AsyncMock()

        await provider.close()

        provider._client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_close_is_idempotent(self):
        """Calling close() multiple times should not raise."""
        provider = OpenAIProvider(
            api_key="test", base_url="https://test.example.com", model="gpt-4o",
        )
        provider._client = MagicMock()
        provider._client.close = AsyncMock()

        await provider.close()
        await provider.close()

        assert provider._client.close.await_count == 2


# ------------------------------------------------------------------ #
# AgenticLoop.shutdown()
# ------------------------------------------------------------------ #


class TestAgenticLoopShutdown:
    """Verify that AgenticLoop.shutdown() cleans up resources."""

    def _make_loop(
        self,
        provider: Provider | None = None,
        session_storage: MagicMock | None = None,
    ) -> AgenticLoop:
        """Create a minimal AgenticLoop for testing shutdown."""
        mock_provider = provider or MockProvider()
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_context = MagicMock()
        mock_context.build_system.return_value = []
        mock_memory = MagicMock()
        return AgenticLoop(
            mock_provider,
            mock_registry,
            mock_context,
            mock_memory,
            session_storage=session_storage,
        )

    @pytest.mark.asyncio
    async def test_shutdown_closes_session_storage(self):
        """shutdown() calls session_storage.close()."""
        storage = MagicMock()
        loop = self._make_loop(session_storage=storage)

        await loop.shutdown()

        storage.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_session_storage(self):
        """shutdown() works fine when session_storage is None."""
        loop = self._make_loop(session_storage=None)
        await loop.shutdown()  # should not raise

    @pytest.mark.asyncio
    async def test_shutdown_cancels_dream_tasks(self):
        """shutdown() cancels all tracked auto-dream tasks."""
        loop = self._make_loop()

        # Create a long-running background task to simulate auto-dream
        async def long_sleep():
            await asyncio.sleep(999)

        task = asyncio.create_task(long_sleep())
        loop._dream_tasks.add(task)

        await loop.shutdown()

        assert task.cancelled()
        assert len(loop._dream_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_handles_already_done_dream_tasks(self):
        """shutdown() handles tasks that have already completed."""
        loop = self._make_loop()

        # Create a task that finishes immediately
        task = asyncio.create_task(asyncio.sleep(0))
        loop._dream_tasks.add(task)
        await asyncio.sleep(0)  # let the task complete

        await loop.shutdown()  # should not raise

        assert len(loop._dream_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self):
        """Calling shutdown() multiple times should not raise."""
        storage = MagicMock()
        loop = self._make_loop(session_storage=storage)

        await loop.shutdown()
        await loop.shutdown()

        assert storage.close.call_count == 2


# ------------------------------------------------------------------ #
# Dream task tracking
# ------------------------------------------------------------------ #


class TestDreamTaskTracking:
    """Verify that auto-dream tasks are properly tracked via the set."""

    def _make_loop(self) -> AgenticLoop:
        mock_provider = MockProvider()
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_context = MagicMock()
        mock_context.build_system.return_value = []
        mock_memory = MagicMock()
        return AgenticLoop(
            mock_provider,
            mock_registry,
            mock_context,
            mock_memory,
        )

    def test_dream_tasks_starts_empty(self):
        """Loop should initialise with an empty dream tasks set."""
        loop = self._make_loop()
        assert isinstance(loop._dream_tasks, set)
        assert len(loop._dream_tasks) == 0

    @pytest.mark.asyncio
    async def test_done_callback_discards_finished_tasks(self):
        """Tasks are removed from the set when they complete via done-callback."""
        loop = self._make_loop()

        task = asyncio.create_task(asyncio.sleep(0))
        loop._dream_tasks.add(task)
        task.add_done_callback(loop._dream_tasks.discard)

        # Await the task to ensure it completes and done-callbacks fire.
        await task

        assert task not in loop._dream_tasks
