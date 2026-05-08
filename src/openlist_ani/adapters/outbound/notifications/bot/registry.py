from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import BotBase


BotBuilder = Callable[[dict[str, Any]], BotBase]


class BotRegistry:
    """Registry for replaceable notification bot adapters."""

    def __init__(self) -> None:
        self._builders: dict[str, BotBuilder] = {}

    def register(self, bot_type: str, builder: BotBuilder) -> None:
        key = self._normalize_type(bot_type)
        if key in self._builders:
            raise ValueError(f"Notification bot already registered: {bot_type}")
        self._builders[key] = builder

    def create(self, bot_type: str, config: dict[str, Any]) -> BotBase:
        key = self._normalize_type(bot_type)
        try:
            return self._builders[key](config)
        except KeyError as e:
            available = ", ".join(sorted(self._builders)) or "<none>"
            raise ValueError(
                f"Unknown notification bot '{bot_type}'. Available: {available}"
            ) from e

    @staticmethod
    def _normalize_type(bot_type: str) -> str:
        normalized = bot_type.strip().lower()
        if not normalized:
            raise ValueError("Notification bot type cannot be empty")
        return normalized
