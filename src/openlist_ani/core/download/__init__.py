"""Download module: task state machine, manager, and downloader implementations."""

from .downloader.base import BaseDownloader, DownloadError
from .downloader.openlist_downloader import OpenListDownloader
from .manager import DownloadManager
from .task import (
    TERMINAL_STATES,
    DownloadState,
    DownloadTask,
    InvalidStateTransitionError,
)

__all__ = [
    "BaseDownloader",
    "DownloadError",
    "DownloadManager",
    "DownloadState",
    "DownloadTask",
    "InvalidStateTransitionError",
    "TERMINAL_STATES",
    "OpenListDownloader",
]
