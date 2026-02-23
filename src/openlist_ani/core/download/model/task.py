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
from typing import Any, Optional

from ...website.model import AnimeResourceInfo


class DownloadState(StrEnum):
    """State machine states for download events."""

    PENDING = "pending"  # Initial state, waiting to start
    DOWNLOADING = "downloading"  # Download in progress
    DOWNLOADED = "downloaded"  # Download complete, waiting for post-processing
    POST_PROCESSING = "processing"  # Post-processing (rename, move, etc.)
    COMPLETED = "completed"  # Successfully finished
    FAILED = "failed"  # Failed state
    CANCELLED = "cancelled"  # Cancelled by user


class InvalidStateTransitionError(Exception):
    """Raised when attempting an invalid state transition."""

    pass


STATE_TRANSITIONS = {
    DownloadState.PENDING: {
        DownloadState.DOWNLOADING,
        DownloadState.CANCELLED,
        DownloadState.FAILED,
    },
    DownloadState.DOWNLOADING: {
        DownloadState.DOWNLOADED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    },
    DownloadState.DOWNLOADED: {
        DownloadState.POST_PROCESSING,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    },
    DownloadState.POST_PROCESSING: {
        DownloadState.COMPLETED,
        DownloadState.FAILED,
        DownloadState.CANCELLED,
    },
    DownloadState.COMPLETED: set(),  # Terminal state
    DownloadState.FAILED: {DownloadState.PENDING},  # Can retry
    DownloadState.CANCELLED: {DownloadState.PENDING},  # Can restart
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
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3

    # Paths
    save_path: str = ""  # Base save directory
    temp_path: Optional[str] = None  # Temporary download directory
    final_path: Optional[str] = None  # save_path + filename_after_rename

    # Download tracking
    downloaded_filename: Optional[str] = None  # Original name of downloaded file
    initial_files: list[str] = field(
        default_factory=list
    )  # Files in temp dir before download

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Resource info (flattened for serialization)
    resource_info: AnimeResourceInfo = field(default_factory=AnimeResourceInfo)

    # Extension point for downloader-specific data
    extra_data: dict[str, Any] = field(default_factory=dict)

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
        save_path: str,
        **kwargs,
    ) -> "DownloadTask":
        """Create a DownloadEvent from AnimeResourceInfo."""
        return cls(
            resource_info=resource_info,
            save_path=save_path,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DownloadTask":
        """Create from dictionary."""
        # Convert state string back to enum
        if isinstance(data.get("state"), str):
            data["state"] = DownloadState(data["state"])

        # Convert resource_info dict back to AnimeResourceInfo object
        if isinstance(data.get("resource_info"), dict):
            resource_data = data["resource_info"]
            # Convert quality and languages enums if they exist
            from ...website.model import LanguageType, VideoQuality

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

        return cls(**data)
