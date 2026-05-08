from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..anime_release import AnimeRelease


class DownloadState(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    RENAMING = "renaming"
    RENAMED = "renamed"
    NOTIFYING = "notifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[DownloadState] = frozenset(
    {
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }
)


@dataclass
class DownloadTask:
    """Internal task context used by downloader adapters."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    release: AnimeRelease = field(
        default_factory=lambda: AnimeRelease(title="", download_url="")
    )
    base_path: str = ""
    target_directory_path: str = ""
    state: DownloadState = DownloadState.PENDING
    output_path: str | None = None
    progress: float | None = None
    downloader_data: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
