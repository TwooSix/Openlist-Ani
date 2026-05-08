"""Retrying notification delivery."""

from __future__ import annotations

import asyncio

from openlist_ani.logger import logger

from .bot.base import BotBase


class RetryingNotificationSink:
    def __init__(self, max_retries: int = 3, retry_backoff: float = 2.0) -> None:
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def send(self, bot: BotBase, message: str) -> bool:
        bot_type = type(bot).__name__
        for attempt in range(1, self._max_retries + 1):
            try:
                if await bot.send_message(message):
                    return True
                logger.debug(
                    f"Notification to {bot_type} failed "
                    f"(attempt {attempt}/{self._max_retries})"
                )
            except Exception as e:
                logger.debug(
                    f"Error sending to {bot_type} "
                    f"(attempt {attempt}/{self._max_retries}): {e}"
                )

            if attempt < self._max_retries:
                backoff = self._retry_backoff * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        return False
