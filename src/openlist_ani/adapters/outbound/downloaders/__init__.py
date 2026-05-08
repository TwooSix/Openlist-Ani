"""Download adapter implementations."""

from .openlist import OpenListDownloader
from .registry import DownloaderRegistry

__all__ = ["DownloaderRegistry", "OpenListDownloader"]
