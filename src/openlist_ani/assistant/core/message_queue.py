"""
MessageQueue — pending user message queue for mid-turn injection.

Borrowed from Claude Code's commandQueue pattern. Messages are enqueued
by frontends during an active AI turn, and drained by AgenticLoop
between tool executions.

Thread-safety: all operations run in a single asyncio event loop,
so no locking is needed. The asyncio.Event is used for future
notification/wait use cases.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class PendingMessage:
    """A user message waiting to be injected into the conversation."""

    content: str
    mode: Literal["prompt", "notification"] = "prompt"
    timestamp: float = field(default_factory=time.time)
    seq: int | None = None


class MessageQueue:
    """Process-local queue for pending user messages.

    Frontends call ``enqueue()`` when a user sends a message during an
    active AI turn.  ``AgenticLoop`` checks ``has_pending_prompts()``
    between tool executions and calls ``drain_prompts()`` to inject them
    into the conversation context.

    Only ``mode="prompt"`` messages trigger mid-turn interruption.
    ``mode="notification"`` messages are left for end-of-turn processing.
    """

    def __init__(self) -> None:
        self._queue: list[PendingMessage] = []
        self._changed = asyncio.Event()
        self._next_seq = 1

    def enqueue(self, message: PendingMessage) -> PendingMessage:
        """Add a message to the queue. Non-blocking."""
        if message.seq is None:
            message.seq = self._next_seq
            self._next_seq += 1
        self._queue.append(message)
        self._changed.set()
        return message

    def has_pending_prompts(self) -> bool:
        """Check if there are any pending user prompt messages."""
        return any(m.mode == "prompt" for m in self._queue)

    def pending_prompt_count(self) -> int:
        """Return the number of queued prompt messages."""
        return sum(1 for m in self._queue if m.mode == "prompt")

    def pending_prompt_seqs(self) -> list[int]:
        """Return sequence ids for queued prompt messages."""
        return [m.seq for m in self._queue if m.mode == "prompt" and m.seq is not None]

    def oldest_prompt_age_ms(self) -> int | None:
        """Return the oldest queued prompt age in milliseconds."""
        prompt_timestamps = [m.timestamp for m in self._queue if m.mode == "prompt"]
        if not prompt_timestamps:
            return None
        return max(0, int((time.time() - min(prompt_timestamps)) * 1000))

    def drain_prompts(self) -> list[PendingMessage]:
        """Remove and return all pending user prompt messages.

        Notification messages are left in the queue.
        Returns messages in enqueue order (oldest first).
        """
        prompts = [m for m in self._queue if m.mode == "prompt"]
        self._queue = [m for m in self._queue if m.mode != "prompt"]
        if not self._queue:
            self._changed.clear()
        return prompts

    def clear(self) -> None:
        """Clear all messages from the queue."""
        self._queue.clear()
        self._changed.clear()

    def __len__(self) -> int:
        return len(self._queue)

    def __bool__(self) -> bool:
        return len(self._queue) > 0
