"""Notification manager factory."""

from __future__ import annotations

from openlist_ani.logger import logger

from .bot.factory import BotFactory
from .manager import NotificationManager
from .settings import NotificationSettings


class NotificationManagerFactory:
    def __init__(self, bot_factory: BotFactory | None = None) -> None:
        self._bot_factory = bot_factory or BotFactory()

    def create(self, settings: NotificationSettings) -> NotificationManager | None:
        if not settings.enabled:
            logger.debug("Notification system is disabled")
            return None

        if not settings.bots:
            logger.warning("Notification enabled but no bots configured")
            return None

        bots = []
        for bot_config in settings.bots:
            if not bot_config.enabled:
                logger.debug(f"Skipping disabled bot: {bot_config.type}")
                continue

            try:
                bot = self._bot_factory.create_bot(bot_config.type, bot_config.config)
                bots.append(bot)
                logger.debug(f"Notification bot enabled: {bot_config.type}")
            except ValueError as e:
                logger.warning(f"Invalid bot configuration: {e}")
            except Exception as e:
                logger.warning(f"Failed to initialize {bot_config.type} bot: {e}")

        if not bots:
            logger.warning("No notification bots were successfully initialized")
            return None

        return NotificationManager(bots, batch_interval=settings.batch_interval)
