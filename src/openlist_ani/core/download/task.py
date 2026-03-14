"""
Download event model with state machine support.

This module defines the DownloadEvent dataclass which represents a download task
with state machine transitions for tracking progress through different stages.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from ..website.model import AnimeResourceInfo


class DownloadState(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidStateTransitionError(Exception):
    """Raised when attempting an invalid state transition."""

    pass


TERMINAL_STATES: frozenset[DownloadState] = frozenset(
    {
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    }
)

STATE_TRANSITIONS: dict[DownloadState, set[DownloadState]] = {
    DownloadState.PENDING: {
        DownloadState.DOWNLOADING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    },
    DownloadState.DOWNLOADING: {
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    },
    DownloadState.COMPLETED: set(),
    DownloadState.FAILED: {DownloadState.PENDING},
    DownloadState.CANCELLED: {DownloadState.PENDING},
}


@dataclass
class DownloadTask:
    """
    Represents a download event with full state tracking.

    This dataclass encapsulates all information needed to track a download
    through its lifecycle, including the ability to serialize/deserialize
    for persistence and recovery.
    """

    # Core identifiers
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # State
    state: DownloadState = DownloadState.PENDING
    error_message: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    progress: float | None = None

    # Paths
    base_path: str = ""  # Root download directory (e.g. /PikPak/Debug)
    output_path: str | None = None  # Final file path after completion

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None

    # Resource info
    resource_info: AnimeResourceInfo = field(default_factory=AnimeResourceInfo)

    # Extension point for downloader-specific data (temp paths, task IDs, etc.)
    downloader_data: dict[str, Any] = field(default_factory=dict)

    def update_state(self, new_state: DownloadState) -> None:
        """Update the state of the download event."""
        if new_state not in STATE_TRANSITIONS[self.state]:
            raise InvalidStateTransitionError(
                f"Invalid state transition from {self.state} to {new_state}"
            )

        self.state = new_state
        self.updated_at = datetime.now().isoformat()

    def mark_failed(self, error_message: str) -> None:
        """Mark the event as failed with an error message."""
        self.error_message = error_message
        self.update_state(DownloadState.FAILED)

    def can_retry(self) -> bool:
        """Check if the event can be retried."""
        return (
            self.state == DownloadState.FAILED and self.retry_count < self.max_retries
        )

    def retry(self) -> None:
        """Reset state for retry."""
        if not self.can_retry():
            raise InvalidStateTransitionError(
                f"Cannot retry: state={self.state}, retries={self.retry_count}/{self.max_retries}"
            )
        self.retry_count += 1
        self.error_message = None
        self.state = DownloadState.PENDING
        self.updated_at = datetime.now().isoformat()

    @classmethod
    def from_resource_info(
        cls,
        resource_info: AnimeResourceInfo,
        base_path: str,
        **kwargs,
    ) -> "DownloadTask":
        """Create a DownloadTask from AnimeResourceInfo."""
        return cls(
            resource_info=resource_info,
            base_path=base_path,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    _STATE_MIGRATION = {
        "downloaded": DownloadState.DOWNLOADING,
        "processing": DownloadState.DOWNLOADING,
        "transferring": DownloadState.DOWNLOADING,
        "cleaning_up": DownloadState.DOWNLOADING,
    }

    # Keys renamed between versions — old name → new field name.
    _FIELD_MIGRATION: ClassVar[dict[str, str]] = {
        "save_path": "base_path",
        "final_path": "output_path",
        "extra_data": "downloader_data",
    }

    # Fields that used to live at top-level but now belong in downloader_data.
    _FIELDS_TO_DOWNLOADER_DATA: ClassVar[set[str]] = {
        "temp_path",
        "initial_files",
        "downloaded_filename",
    }

    @classmethod
    def _migrate_resource_info(cls, data: dict[str, Any]) -> None:
        """Convert a raw resource_info dict to an AnimeResourceInfo object."""
        resource_data = data["resource_info"]
        from ..website.model import LanguageType, VideoQuality

        if "quality" in resource_data and isinstance(resource_data["quality"], str):
            resource_data["quality"] = VideoQuality(resource_data["quality"])
        if "languages" in resource_data and isinstance(
            resource_data["languages"], list
        ):
            resource_data["languages"] = [
                LanguageType(lang) if isinstance(lang, str) else lang
                for lang in resource_data["languages"]
            ]
        data["resource_info"] = AnimeResourceInfo(**resource_data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DownloadTask":
        """Create from dictionary, with backward-compatible field migration."""
        if isinstance(data.get("state"), str):
            raw = data["state"]
            data["state"] = cls._STATE_MIGRATION.get(raw, DownloadState(raw))

        # Migrate renamed fields
        for old_key, new_key in cls._FIELD_MIGRATION.items():
            if old_key in data and new_key not in data:
                data[new_key] = data.pop(old_key)

        # Migrate removed top-level fields into downloader_data
        dd = data.setdefault("downloader_data", {})
        if not isinstance(dd, dict):
            dd = {}
            data["downloader_data"] = dd
        for key in cls._FIELDS_TO_DOWNLOADER_DATA:
            if key in data:
                dd.setdefault(key, data.pop(key))

        if isinstance(data.get("resource_info"), dict):
            cls._migrate_resource_info(data)

        return cls(**data)
