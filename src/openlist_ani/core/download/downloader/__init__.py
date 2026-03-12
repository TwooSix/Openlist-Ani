"""Downloader implementations module.

Each downloader lives in its own sub-package (e.g. ``openlist/``).
New downloaders should subclass ``BaseDownloader`` and register here.
"""

from .base import BaseDownloader, DownloadError
from .openlist_downloader import OpenListDownloader

__all__ = [
    "BaseDownloader",
    "DownloadError",
    "OpenListDownloader",
]
