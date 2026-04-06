"""Tests for context collapse (stub framework)."""

import pytest

from openlist_ani.assistant.core.context_collapse import (
    ContextCollapse,
    ContextCollapseStats,
)
from openlist_ani.assistant.core.models import Message, Role


class TestContextCollapseStats:
    """Tests for ContextCollapseStats."""

    def test_initial_state(self):
        stats = ContextCollapseStats()
        assert stats.collapsed_spans == 0
        assert stats.empty_spawn_warning_emitted is False

    def test_reset(self):
        stats = ContextCollapseStats()
        stats.collapsed_spans = 5
        stats.empty_spawn_warning_emitted = True
        stats.reset()
        assert stats.collapsed_spans == 0
        assert stats.empty_spawn_warning_emitted is False


class TestContextCollapse:
    """Tests for the ContextCollapse stub framework."""

    def test_disabled_by_default(self):
        """Context collapse should be disabled by default."""
        cc = ContextCollapse()
        assert cc.enabled is False

    def test_init_is_noop(self):
        """init() should be a no-op."""
        cc = ContextCollapse()
        cc.init()  # Should not raise

    def test_reset_clears_stats(self):
        """reset() should reset stats."""
        cc = ContextCollapse()
        cc.stats.collapsed_spans = 10
        cc.reset()
        assert cc.stats.collapsed_spans == 0

    @pytest.mark.asyncio
    async def test_apply_collapses_returns_none_when_disabled(self):
        """apply_collapses_if_needed should return None when disabled."""
        cc = ContextCollapse()
        messages = [
            Message(role=Role.SYSTEM, content="System"),
            Message(role=Role.USER, content="Hello"),
        ]
        result = await cc.apply_collapses_if_needed(messages)
        assert result is None

    def test_is_withheld_prompt_too_long_returns_false(self):
        """Should always return False (stub)."""
        cc = ContextCollapse()
        msg = Message(role=Role.USER, content="test")
        assert cc.is_withheld_prompt_too_long(msg) is False

    def test_recover_from_overflow_returns_none(self):
        """Should return None when disabled (stub)."""
        cc = ContextCollapse()
        messages = [Message(role=Role.USER, content="test")]
        result = cc.recover_from_overflow(messages)
        assert result is None

    def test_subscribe_returns_unsubscribe(self):
        """subscribe() should return an unsubscribe function."""
        cc = ContextCollapse()

        def callback() -> None:
            pass

        unsub = cc.subscribe(callback)
        assert callable(unsub)
        # Should not raise
        unsub()

    def test_subscribe_and_unsubscribe(self):
        """Should properly track and remove subscribers."""
        cc = ContextCollapse()
        callbacks: list[bool] = []

        def cb() -> None:
            callbacks.append(True)

        unsub = cc.subscribe(cb)
        assert len(cc._subscribers) == 1
        unsub()
        assert len(cc._subscribers) == 0
