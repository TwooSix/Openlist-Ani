from __future__ import annotations

from typing import Any

from .base import BotBase
from .pushplus import PushPlusBot
from .registry import BotRegistry
from .telegram import TelegramBot


class BotFactory:
    """Create notification bots through a replaceable registry."""

    def __init__(self, registry: BotRegistry | None = None) -> None:
        self._registry = registry or default_bot_registry()

    def create_bot(self, bot_type: str, config: dict[str, Any]) -> BotBase:
        return self._registry.create(bot_type, config)


def default_bot_registry() -> BotRegistry:
    registry = BotRegistry()
    registry.register("telegram", _create_telegram)
    registry.register("pushplus", _create_pushplus)
    return registry


def _create_telegram(config: dict[str, Any]) -> BotBase:
    bot_token = config.get("bot_token")
    user_id = config.get("user_id")
    if not bot_token or not user_id:
        raise ValueError("Telegram bot requires 'bot_token' and 'user_id' in config")
    return TelegramBot(bot_token=bot_token, user_id=user_id)


def _create_pushplus(config: dict[str, Any]) -> BotBase:
    user_token = config.get("user_token")
    if not user_token:
        raise ValueError("PushPlus bot requires 'user_token' in config")
    channel = config.get("channel")
    return PushPlusBot(user_token=user_token, channel=channel)
