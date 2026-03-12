"""
Download module for managing anime downloads.

This module provides a flexible download architecture with:
- DownloadTask: State machine-based download task tracking
- DownloadManager: Orchestrates downloads and manages persistence
- BaseDownloader: Abstract interface for download implementations
- OpenListDownloader: OpenList-based downloader implementation

Each downloader lives in its own sub-package under ``downloader/``.
Currently only the OpenList downloader is implemented, but the
architecture supports adding new downloaders by subclassing
``BaseDownloader``.

Usage:
    from openlist_ani.core.download import (
        DownloadManager,
        DownloadTask,
        DownloadState,
        BaseDownloader,
        OpenListDownloader,
    )

    # Create a downloader instance
    downloader = OpenListDownloader(
        base_url="http://localhost:5244",
        token="<token>",
        offline_download_tool="qBittorrent",
        rename_format="{anime_name} S{season:02d}E{episode:02d}",
    )

    # Create manager instance
    manager = DownloadManager(downloader)
    # Pending downloads are automatically recovered on initialization

    # Start a download
    await manager.download(resource_info, save_path)
"""

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
