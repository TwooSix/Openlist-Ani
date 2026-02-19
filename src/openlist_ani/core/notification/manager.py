"""
Notification manager module.

This module provides the NotificationManager class which manages multiple
notification bots and handles sending notifications to all configured channels.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from openlist_ani.logger import logger

if TYPE_CHECKING:
    from openlist_ani.config import NotificationConfig

    from .bot.base import BotBase


class NotificationManager:
    """Manager for handling multiple notification channels with batching support."""

    def __init__(
        self,
        bots: list[BotBase] | None = None,
        batch_interval: float = 300.0,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ):
        """
        Initialize notification manager with a list of bots.

        Args:
            bots: List of bot instances to send notifications to.
                  If None, no notifications will be sent.
            batch_interval: Time interval (in seconds) to batch notifications.
                           Default is 300 seconds (5 minutes). Set to 0 to disable batching.
            max_retries: Maximum number of retries for failed notifications.
            retry_backoff: Initial backoff in seconds between retries.
        """
        self._bots: list[BotBase] = bots or []
        self._batch_interval = batch_interval
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

        # Queue per bot: {bot_instance: {anime_name: [titles]}}
        self._bot_queues: dict[BotBase, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # Initialize queues for existing bots
        for bot in self._bots:
            _ = self._bot_queues[bot]

        self._batch_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._running = False

    def add_bot(self, bot: BotBase) -> None:
        """
        Add a bot to the notification manager.

        Args:
            bot: Bot instance to add
        """
        self._bots.append(bot)
        # Ensure queue exists
        _ = self._bot_queues[bot]

    def start(self) -> None:
        """Start the batch notification worker."""
        if self._batch_interval > 0 and not self._running:
            self._running = True
            self._batch_task = asyncio.create_task(self._batch_worker())
            logger.info(
                f"Notification batching enabled (sends every {int(self._batch_interval / 60)} minutes)"
            )

    async def stop(self) -> None:
        """Stop the batch notification worker and send any pending notifications."""
        self._running = False
        try:
            if self._batch_task:
                self._batch_task.cancel()
                await self._batch_task
        finally:
            # Send any remaining notifications before propagating cancellation.
            await self._send_batched_notifications()
            logger.debug("Notification manager stopped")

    async def _batch_worker(self) -> None:
        """Background worker that periodically sends batched notifications."""
        while self._running:
            try:
                await asyncio.sleep(self._batch_interval)
                await self._send_batched_notifications()
            except asyncio.CancelledError:
                logger.debug("Batch worker cancelled")
                raise
            except Exception as e:
                logger.error(f"Error in batch worker: {e}")

    async def _send_batched_notifications(self) -> None:
        """Send pending notifications for each bot with retry logic."""
        async with self._lock:
            for bot in self._bots:
                queue = self._bot_queues[bot]
                if not queue:
                    continue

                # Build the notification message
                message_parts = ["你订阅的番剧更新啦："]
                count = 0
                for anime_name, titles in queue.items():
                    message_parts.append(f"\n[{anime_name}]:")
                    for title in titles:
                        message_parts.append(f"  • {title}")
                        count += 1

                message = "\n".join(message_parts)

                # Send with retry
                if await self._send_with_retry(bot, message):
                    queue.clear()
                    logger.info(
                        f"Sent batch notification ({count} items) via {type(bot).__name__}"
                    )
                else:
                    logger.warning(
                        f"Failed to send batch notification via {type(bot).__name__} after retries. "
                        f"Keeping {count} items in {type(bot).__name__} queue."
                    )

    async def _send_with_retry(self, bot: BotBase, message: str) -> bool:
        """Send message to a bot with exponential backoff retries."""
        bot_type = type(bot).__name__
        for attempt in range(1, self._max_retries + 1):
            try:
                if await bot.send_message(message):
                    return True
                logger.warning(
                    f"Notification to {bot_type} failed (attempt {attempt}/{self._max_retries})"
                )
            except Exception as e:
                logger.error(
                    f"Error sending to {bot_type} (attempt {attempt}/{self._max_retries}): {e}"
                )

            if attempt < self._max_retries:
                backoff = self._retry_backoff * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        return False

    async def send_notification(self, message: str) -> dict[str, bool]:
        """
        Send notification to all configured bots.

        Args:
            message: Message content to send

        Returns:
            Dictionary mapping bot type to success status
        """
        if not self._bots:
            logger.debug("No notification bots configured, skipping notification")
            return {}

        results = {}
        for bot in self._bots:
            bot_type = type(bot).__name__
            success = await self._send_with_retry(bot, message)
            results[bot_type] = success
            if success:
                logger.info(f"Notification sent via {bot_type}")
            else:
                logger.warning(
                    f"Failed to send notification via {bot_type} after retries"
                )

        return results

    async def send_download_complete_notification(
        self, anime_name: str, title: str
    ) -> dict[str, bool]:
        """
        Send a download complete notification.

        If batching is enabled, adds the task to the pending queue.
        If batching is disabled, sends immediately.

        Args:
            anime_name: Name of the anime series
            title: Full title of the downloaded episode

        Returns:
            Dictionary mapping bot type to success status (empty dict if batched)
        """
        if self._batch_interval > 0:
            # Batching enabled - add to queue
            async with self._lock:
                for bot in self._bots:
                    self._bot_queues[bot][anime_name].append(title)

                total_pending = sum(
                    sum(len(titles) for titles in q.values())
                    for q in self._bot_queues.values()
                )
                logger.debug(
                    f"Added to notification queues: [{anime_name}] {title} "
                    f"(total pending items: {total_pending})"
                )
            return {}
        else:
            # Batching disabled - send immediately
            message = f"你订阅的番剧[{anime_name}] 更新啦：\n{title}\n"
            return await self.send_notification(message)

    @classmethod
    def from_config(cls, config: NotificationConfig) -> NotificationManager | None:
        """
        Create NotificationManager from configuration.

        Args:
            config: NotificationConfig instance

        Returns:
            NotificationManager instance if enabled, None otherwise
        """
        if not config.enabled:
            logger.debug("Notification system is disabled")
            return None

        if not config.bots:
            logger.warning("Notification enabled but no bots configured")
            return None

        from .bot.base import BotType
        from .bot.factory import BotFactory

        bots = []
        for bot_config in config.bots:
            if not bot_config.enabled:
                logger.debug(f"Skipping disabled bot: {bot_config.type}")
                continue

            try:
                bot_type = BotType(bot_config.type)
                bot = BotFactory.create_bot(bot_type, bot_config.config)
                bots.append(bot)
                logger.info(f"Notification bot enabled: {bot_config.type}")
            except ValueError as e:
                logger.error(f"Invalid bot configuration: {e}")
            except Exception as e:
                logger.error(f"Failed to initialize {bot_config.type} bot: {e}")

        if not bots:
            logger.warning("No notification bots were successfully initialized")
            return None

        # Get batch interval from config, default to 5 minutes
        batch_interval = getattr(config, "batch_interval", 300.0)
        return cls(bots, batch_interval=batch_interval)
