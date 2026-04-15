"""
Context collapse — granular context management layer.

- Separate from autocompact (which summarizes entire conversations)
- Context collapse selectively collapses old tool results, file reads,
  and other bulky content while preserving the conversational structure
- Currently a stub framework

The idea is to collapse verbose tool results into short summaries
(e.g., "Read 500 lines from src/main.py" instead of the full content)
when the context grows large, before resorting to full autocompact.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openlist_ani.assistant.core.models import Message

from loguru import logger


class ContextCollapseStats:
    """Statistics about context collapse operations."""

    def __init__(self) -> None:
        self.collapsed_spans: int = 0
        self.empty_span_warning_emitted: bool = False

    def reset(self) -> None:
        """Reset stats."""
        self.collapsed_spans = 0
        self.empty_span_warning_emitted = False


class ContextCollapse:
    """Granular context management layer.

    Currently a stub framework — all operations are no-ops.
    The structure is in place for future implementation.

    Future behavior (when enabled):
    - Tracks "spans" of related messages (e.g., a tool call + result)
    - When context grows large, collapses old spans into summaries
    - Preserves the most recent spans in full detail
    - Different from autocompact: preserves message structure, only
      truncates content within messages

    Usage in the agentic loop:
    1. After each tool result, register the span
    2. Before each LLM call, apply collapses if needed
    3. On prompt-too-long, try recovery via aggressive collapse
    """

    def __init__(self) -> None:
        self._enabled = False
        self._stats = ContextCollapseStats()
        self._subscribers: list[object] = []

    @property
    def enabled(self) -> bool:
        """Whether context collapse is enabled."""
        return self._enabled

    @property
    def stats(self) -> ContextCollapseStats:
        """Current collapse statistics."""
        return self._stats

    def init(self) -> None:
        """Initialize the context collapse system.

        Currently a no-op.
        """
        logger.debug("Context collapse initialized (stub)")

    def reset(self) -> None:
        """Reset state."""
        self._stats.reset()
        logger.debug("Context collapse reset (stub)")

    def apply_collapses_if_needed(
        self,
        messages: list[Message],
    ) -> list[Message] | None:
        """Apply context collapse to messages if needed.

        Currently a no-op — returns None (no changes).

        Args:
            messages: Current conversation messages.

        Returns:
            Modified message list if collapses were applied,
            None if no changes needed.
        """
        if not self._enabled:
            return None

        # Future: implement selective collapse of old tool results
        return None

    def is_withheld_prompt_too_long(self, message: Message) -> bool:
        """Check if a message was withheld due to prompt-too-long.

        Currently always returns False.
        """
        return False

    def recover_from_overflow(
        self,
        _messages: list[Message],
    ) -> list[Message] | None:
        """Try to recover from context overflow by aggressively collapsing.

        Currently a no-op — returns None.

        Args:
            _messages: Current conversation messages (unused in no-op stub).

        Returns:
            Modified message list if recovery succeeded,
            None if recovery is not possible.
        """
        if not self._enabled:
            return None

        # Future: implement aggressive collapse for overflow recovery
        return None

    def subscribe(self, callback: object) -> Callable[[], None]:
        """Subscribe to collapse events.

        Returns an unsubscribe function.
        """
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe
