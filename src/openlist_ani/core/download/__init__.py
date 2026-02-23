"""
Download module for managing anime downloads.

This module provides a flexible download architecture with:
- DownloadEvent: State machine-based download event tracking
- DownloadManager: Orchestrates downloads and manages persistence
- BaseDownloader: Abstract interface for download implementations
- OpenListDownloader: OpenList-based downloader implementation

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

from .downloader.base import BaseDownloader, HandlerResult, HandlerStatus
from .downloader.openlist_downloader import OpenListDownloader
from .manager import DownloadManager
from .model.task import (
    DownloadState,
    DownloadTask,
    InvalidStateTransitionError,
)

__all__ = [
    "DownloadTask",
    "DownloadState",
    "InvalidStateTransitionError",
    "BaseDownloader",
    "HandlerResult",
    "HandlerStatus",
    "DownloadManager",
    "OpenListDownloader",
]
