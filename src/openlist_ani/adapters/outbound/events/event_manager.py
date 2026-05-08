from __future__ import annotations

import asyncio
import traceback
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from openlist_ani.application.common import OAniEvent, OAniEventType
from openlist_ani.logger import logger, sanitize_for_log

EventHandler = Callable[[OAniEvent], Any | Awaitable[Any]]


class OAniEventManager:
    """Small async event manager for application-level events.

    The manager owns its subscriber table and bounded history. Publishing records
    the event synchronously, then dispatches handlers as background tasks when
    running. Handler exceptions are logged and isolated from publishers.
    """

    def __init__(self, history_limit: int = 500) -> None:
        self._subscribers: dict[OAniEventType, set[EventHandler]] = defaultdict(set)
        self._history: deque[OAniEvent] = deque(maxlen=history_limit)
        self._lock = asyncio.Lock()
        self._pending: set[asyncio.Task[None]] = set()
        self._running = False

    async def start(self) -> None:
        async with self._lock:
            self._running = True

    async def stop(self) -> None:
        self._running = False
        pending = tuple(self._pending)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending.clear()

    async def subscribe(self, event_type: OAniEventType, handler: EventHandler) -> None:
        async with self._lock:
            self._subscribers[event_type].add(handler)

    async def unsubscribe(
        self, event_type: OAniEventType, handler: EventHandler
    ) -> None:
        async with self._lock:
            handlers = self._subscribers.get(event_type)
            if handlers:
                handlers.discard(handler)

    async def publish(self, event: OAniEvent) -> None:
        async with self._lock:
            self._history.append(event)
            handlers = tuple(self._subscribers.get(event.event_type, ()))

        if not self._running:
            return

        for handler in handlers:
            task = asyncio.create_task(self._invoke(handler, event))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def history(
        self, event_type: OAniEventType | None = None, limit: int | None = None
    ) -> list[OAniEvent]:
        async with self._lock:
            events = [
                event
                for event in self._history
                if event_type is None or event.event_type == event_type
            ]
        return events[-limit:] if limit is not None else events

    async def drain(self) -> None:
        """Wait for already scheduled handlers. Intended for tests/shutdown hooks."""
        pending = tuple(self._pending)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _invoke(self, handler: EventHandler, event: OAniEvent) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            handler_name = getattr(handler, "__name__", type(handler).__name__)
            logger.warning(
                f"Event handler {handler_name} failed for {event.event_type}; "
                f"pipeline will continue: {e}"
            )
            trace = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            logger.debug(f"Event handler failure traceback: {sanitize_for_log(trace)}")
