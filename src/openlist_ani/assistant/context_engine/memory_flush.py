"""Pre-compaction memory flush — Compaction Ping.

Ported from OpenClaw's ``memory-flush.ts``.

When a session approaches the compaction threshold the assistant should
persist durable memories *before* the context is summarised away.  This
module provides the threshold check and the prompt text.

Key safeguard: **one flush per compaction cycle** — tracked via
``MemoryFlushGuard`` so the LLM is not spammed with flush requests
on every subsequent turn after crossing the threshold.
"""

from __future__ import annotations

from .settings import CompactionSettings

_FLUSH_SYSTEM_PROMPT = (
    "Pre-compaction memory flush turn. "
    "The session is near auto-compaction; store durable memories to disk now.\n\n"
    "- Use `update_memory` for important facts, task outcomes, and workflow patterns.\n"
    "- Use `update_user_profile` for personal user info discovered in this session.\n"
    "- Do NOT repeat information already stored in MEMORY.md or USER.md.\n"
    "- If nothing new to store, continue normally."
)


class MemoryFlushGuard:
    """Track whether a memory flush has been triggered this compaction cycle.

    Call :meth:`reset` after each compaction to allow the next flush.
    """

    def __init__(self) -> None:
        self._flushed_at_compaction: int = -1
        self._compaction_count: int = 0

    @property
    def compaction_count(self) -> int:
        """Current compaction cycle counter."""
        return self._compaction_count

    def record_compaction(self) -> None:
        """Bump the compaction counter (call after each compaction)."""
        self._compaction_count += 1

    def should_flush(
        self,
        estimated_tokens: int,
        settings: CompactionSettings,
    ) -> bool:
        """Return True if a memory flush should run now.

        Args:
            estimated_tokens: Current session token estimate.
            settings: Compaction configuration.

        Returns:
            True if tokens exceed the flush threshold *and* no flush
            has been performed in the current compaction cycle.
        """
        threshold = int(settings.session_max_tokens * settings.memory_flush_threshold)
        if estimated_tokens < threshold:
            return False
        # One flush per compaction cycle.
        if self._flushed_at_compaction == self._compaction_count:
            return False
        return True

    def record_flush(self) -> None:
        """Mark the flush as done for the current compaction cycle."""
        self._flushed_at_compaction = self._compaction_count


def build_flush_system_message() -> dict[str, str]:
    """Return the system message injected when a memory flush is needed."""
    return {"role": "system", "content": _FLUSH_SYSTEM_PROMPT}
