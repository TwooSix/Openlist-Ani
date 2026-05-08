from __future__ import annotations

from collections.abc import Callable

from openlist_ani.application.anime_library_ingestion.ports import DownloaderPort


DownloaderFactory = Callable[[], DownloaderPort]


class DownloaderRegistry:
    """Runtime registry for replaceable downloader adapters."""

    def __init__(self) -> None:
        self._factories: dict[str, DownloaderFactory] = {}

    def register(self, name: str, factory: DownloaderFactory) -> None:
        key = self._normalize_name(name)
        if key in self._factories:
            raise ValueError(f"Downloader already registered: {name}")
        self._factories[key] = factory

    def create(self, name: str) -> DownloaderPort:
        key = self._normalize_name(name)
        try:
            return self._factories[key]()
        except KeyError as e:
            available = ", ".join(sorted(self._factories)) or "<none>"
            raise ValueError(
                f"Unknown downloader '{name}'. Available: {available}"
            ) from e

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Downloader name cannot be empty")
        return normalized
