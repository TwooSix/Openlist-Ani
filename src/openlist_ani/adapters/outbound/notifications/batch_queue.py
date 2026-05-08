"""Per-channel notification batch queue."""

from __future__ import annotations

from collections import defaultdict

from .bot.base import BotBase


class NotificationBatchQueue:
    def __init__(self, bots: list[BotBase]) -> None:
        self._queues: dict[BotBase, dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for bot in bots:
            self.ensure_bot(bot)

    def ensure_bot(self, bot: BotBase) -> None:
        _ = self._queues[bot]

    def add_download(self, bots: list[BotBase], anime_name: str, title: str) -> int:
        for bot in bots:
            self._queues[bot][anime_name].append(title)
        return self.total_pending()

    def queue_for(self, bot: BotBase) -> dict[str, list[str]]:
        return self._queues[bot]

    def clear(self, bot: BotBase) -> None:
        self._queues[bot].clear()

    def total_pending(self) -> int:
        return sum(
            sum(len(titles) for titles in queue.values())
            for queue in self._queues.values()
        )
