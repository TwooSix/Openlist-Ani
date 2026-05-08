"""
Notification module for sending updates to various channels.
"""

from .bot.base import BotBase
from .bot.factory import BotFactory, default_bot_registry
from .bot.registry import BotRegistry
from .factory import NotificationManagerFactory
from .manager import NotificationManager
from .settings import NotificationBotSettings, NotificationSettings

__all__ = [
    "BotBase",
    "BotFactory",
    "BotRegistry",
    "NotificationBotSettings",
    "NotificationManager",
    "NotificationManagerFactory",
    "NotificationSettings",
    "default_bot_registry",
]
