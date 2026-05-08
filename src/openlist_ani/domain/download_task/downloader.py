from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..anime_release import AnimeRelease


class DownloadError(Exception):
    """Raised when a download fails permanently."""


class DownloaderCapabilityError(DownloadError):
    """Raised when a downloader cannot provide a requested capability."""


@dataclass
class DownloaderMemento:
    downloader_type: str
    payload: dict[str, Any] = field(default_factory=dict)


DownloadCheckpointCallback = Callable[[DownloaderMemento], Awaitable[None]]


@dataclass
class DownloadRequest:
    release: AnimeRelease
    base_path: str
    target_directory_path: str
    downloader_memento: DownloaderMemento | None = None
    checkpoint_callback: DownloadCheckpointCallback | None = None


@dataclass
class DownloadedFile:
    release: AnimeRelease
    directory_path: str
    filename: str
    downloader_memento: DownloaderMemento

    @property
    def path(self) -> str:
        return f"{self.directory_path.rstrip('/')}/{self.filename}"
