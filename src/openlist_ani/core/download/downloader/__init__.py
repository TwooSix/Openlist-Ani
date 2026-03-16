"""Downloader implementations — subclass ``BaseDownloader`` to add new ones."""

from .base import BaseDownloader, DownloadError
from .openlist_downloader import OpenListDownloader

__all__ = [
    "BaseDownloader",
    "DownloadError",
    "OpenListDownloader",
]
