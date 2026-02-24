from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from ..model.task import DownloadTask


class HandlerStatus(StrEnum):
    DONE = "done"
    POLL = "poll"
    FAILED = "failed"


@dataclass
class HandlerResult:
    status: HandlerStatus
    error_message: Optional[str] = None
    poll_delay: float = 0.0

    @classmethod
    def done(cls) -> "HandlerResult":
        return cls(status=HandlerStatus.DONE)

    @classmethod
    def poll(cls, delay: float = 5.0) -> "HandlerResult":
        return cls(status=HandlerStatus.POLL, poll_delay=delay)

    @classmethod
    def fail(cls, message: str) -> "HandlerResult":
        return cls(status=HandlerStatus.FAILED, error_message=message)


class BaseDownloader(ABC):

    @property
    @abstractmethod
    def downloader_type(self) -> str: ...

    @abstractmethod
    async def on_pending(self, task: DownloadTask) -> HandlerResult:
        """Prepare and start the download."""

    @abstractmethod
    async def on_downloading(self, task: DownloadTask) -> HandlerResult:
        """Check download progress."""

    @abstractmethod
    async def on_transferring(self, task: DownloadTask) -> HandlerResult:
        """Rename and move downloaded file to its final destination."""

    @abstractmethod
    async def on_cleaning_up(self, task: DownloadTask) -> HandlerResult:
        """Clean up temp files/directories."""

    @abstractmethod
    async def on_failed(self, task: DownloadTask) -> None:
        """Post-process after a download has failed (e.g. clean up temp files)."""
