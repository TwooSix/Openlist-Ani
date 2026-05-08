"""Notification manager facade."""

from __future__ import annotations

import asyncio
import contextlib

from openlist_ani.logger import logger

from .batch_queue import NotificationBatchQueue
from .bot.base import BotBase
from .formatter import NotificationFormatter
from .retry import RetryingNotificationSink


class NotificationManager:
    """Facade for notification delivery with optional batching."""

    def __init__(
        self,
        bots: list[BotBase] | None = None,
        batch_interval: float = 300.0,
        sink: RetryingNotificationSink | None = None,
        formatter: NotificationFormatter | None = None,
        batch_queue: NotificationBatchQueue | None = None,
    ) -> None:
        self._bots: list[BotBase] = bots or []
        self._batch_interval = batch_interval
        self._sink = sink or RetryingNotificationSink()
        self._formatter = formatter or NotificationFormatter()
        self._batch_queue = batch_queue or NotificationBatchQueue(self._bots)

        self._batch_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._running = False

    def add_bot(self, bot: BotBase) -> None:
        self._bots.append(bot)
        self._batch_queue.ensure_bot(bot)

    def start(self) -> None:
        if self._batch_interval > 0 and not self._running:
            self._running = True
            self._batch_task = asyncio.create_task(self._batch_worker())
            logger.debug(
                f"Notification batching enabled "
                f"(sends every {int(self._batch_interval / 60)} minutes)"
            )

    async def stop(self) -> None:
        self._running = False
        if self._batch_task:
            self._batch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._batch_task
        await self._send_batched_notifications()
        logger.debug("Notification manager stopped")

    async def send_notification(self, message: str) -> dict[str, bool]:
        if not self._bots:
            logger.debug("No notification bots configured, skipping notification")
            return {}

        results: dict[str, bool] = {}
        for idx, bot in enumerate(self._bots):
            bot_type = type(bot).__name__
            key = bot_type if bot_type not in results else f"{bot_type}_{idx}"
            success = await self._sink.send(bot, message)
            results[key] = success
            if success:
                logger.debug(f"Notification sent via {bot_type}")
            else:
                logger.warning(
                    f"Failed to send notification via {bot_type} after retries"
                )

        return results

    async def send_download_complete_notification(
        self, anime_name: str, title: str
    ) -> dict[str, bool]:
        if self._batch_interval > 0:
            async with self._lock:
                total_pending = self._batch_queue.add_download(
                    self._bots, anime_name, title
                )
                logger.debug(
                    f"Added to notification queues: [{anime_name}] {title} "
                    f"(total pending items: {total_pending})"
                )
            return {}

        message = self._formatter.download_complete_message(anime_name, title)
        return await self.send_notification(message)

    async def _batch_worker(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._batch_interval)
                await self._send_batched_notifications()
            except asyncio.CancelledError:
                logger.debug("Batch worker cancelled")
                raise
            except Exception as e:
                logger.warning(f"Notification batch worker recovered from error: {e}")

    async def _send_batched_notifications(self) -> None:
        async with self._lock:
            for bot in self._bots:
                queue = self._batch_queue.queue_for(bot)
                if not queue:
                    continue

                message, count = self._formatter.batch_message(queue)
                if await self._sink.send(bot, message):
                    self._batch_queue.clear(bot)
                    logger.debug(
                        f"Sent batch notification ({count} items) "
                        f"via {type(bot).__name__}"
                    )
                else:
                    logger.warning(
                        f"Failed to send batch notification via {type(bot).__name__} "
                        f"after retries. Keeping {count} items in "
                        f"{type(bot).__name__} queue."
                    )
