"""Cooperative cancellation token for async pipelines.

Inspired by JavaScript's AbortController/AbortSignal pattern.
Used to propagate cancellation from the frontend through the
AgenticLoop to streaming and tool execution layers.
"""

from __future__ import annotations

import asyncio


class CancellationToken:
    """Cooperative cancellation token for async pipelines.

    Thread-safe via asyncio.Event. A single token is created per
    user turn and passed through the processing pipeline. The
    frontend calls ``cancel()`` when ESC or Ctrl+C is pressed;
    each layer checks ``is_cancelled`` at its checkpoints.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation. Idempotent."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._event.is_set()

    async def wait(self) -> None:
        """Block until cancellation is signalled."""
        await self._event.wait()
