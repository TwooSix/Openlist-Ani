from __future__ import annotations

from collections.abc import Callable

from openlist_ani.application.anime_library_ingestion.ports import FileRenamerPort

FileRenamerFactory = Callable[[], FileRenamerPort]


class FileRenamerRegistry:
    """Runtime registry for replaceable file rename adapters."""

    def __init__(self) -> None:
        self._factories: dict[str, FileRenamerFactory] = {}

    def register(self, name: str, factory: FileRenamerFactory) -> None:
        key = self._normalize_name(name)
        if key in self._factories:
            raise ValueError(f"File renamer already registered: {name}")
        self._factories[key] = factory

    def create(self, name: str) -> FileRenamerPort:
        key = self._normalize_name(name)
        try:
            return self._factories[key]()
        except KeyError as e:
            available = ", ".join(sorted(self._factories)) or "<none>"
            raise ValueError(
                f"Unknown file renamer '{name}'. Available: {available}"
            ) from e

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("File renamer name cannot be empty")
        return normalized
