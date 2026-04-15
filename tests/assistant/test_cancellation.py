"""Tests for the CancellationToken."""

from __future__ import annotations

import asyncio

import pytest

from openlist_ani.assistant.core.cancellation import CancellationToken


class TestCancellationToken:
    """Unit tests for CancellationToken."""

    def test_initial_state_is_not_cancelled(self):
        """A fresh token should not be cancelled."""
        token = CancellationToken()
        assert token.is_cancelled is False

    def test_cancel_sets_cancelled(self):
        """Calling cancel() should set is_cancelled to True."""
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_cancel_is_idempotent(self):
        """Calling cancel() twice should not raise."""
        token = CancellationToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled is True

    @pytest.mark.asyncio
    async def test_wait_resolves_on_cancel(self):
        """wait() should return once cancel() is called."""
        token = CancellationToken()

        async def _cancel_after_delay():
            await asyncio.sleep(0.01)
            token.cancel()

        task = asyncio.create_task(_cancel_after_delay())
        await asyncio.wait_for(token.wait(), timeout=1.0)
        assert token.is_cancelled is True
        await task

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_if_already_cancelled(self):
        """wait() should return immediately if already cancelled."""
        token = CancellationToken()
        token.cancel()
        await asyncio.wait_for(token.wait(), timeout=0.1)
