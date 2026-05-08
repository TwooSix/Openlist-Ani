from __future__ import annotations

from collections.abc import Callable

from openlist_ani.application.anime_library_ingestion.ports import MetadataParserPort


MetadataParserFactory = Callable[[], MetadataParserPort]


class MetadataParserRegistry:
    """Runtime registry for replaceable metadata parser adapters."""

    def __init__(self) -> None:
        self._factories: dict[str, MetadataParserFactory] = {}

    def register(self, name: str, factory: MetadataParserFactory) -> None:
        key = self._normalize_name(name)
        if key in self._factories:
            raise ValueError(f"Metadata parser already registered: {name}")
        self._factories[key] = factory

    def create(self, name: str) -> MetadataParserPort:
        key = self._normalize_name(name)
        try:
            return self._factories[key]()
        except KeyError as e:
            available = ", ".join(sorted(self._factories)) or "<none>"
            raise ValueError(
                f"Unknown metadata parser '{name}'. Available: {available}"
            ) from e

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Metadata parser name cannot be empty")
        return normalized
