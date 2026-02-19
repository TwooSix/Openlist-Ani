"""
Data models for assistant module.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SearchResult:
    """Search result from anime resource websites."""

    title: str
    download_url: str
    is_downloaded: bool
    anime_name: Optional[str] = None
    episode: Optional[int] = None
    quality: Optional[str] = None


@dataclass
class DownloadResult:
    """Result of download operation."""

    success_count: int
    skipped_count: int
    failed_count: int
    success_items: List[str]
    skipped_items: List[tuple[str, str]]  # (title, reason)
    failed_items: List[tuple[str, str]]  # (title, error)
