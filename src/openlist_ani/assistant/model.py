"""
Data models for assistant module.
"""

from dataclasses import dataclass


@dataclass
class SearchResult:
    """Search result from anime resource websites."""

    title: str
    download_url: str
    is_downloaded: bool
    anime_name: str | None = None
    episode: int | None = None
    quality: str | None = None


@dataclass
class DownloadResult:
    """Result of download operation."""

    success_count: int
    skipped_count: int
    failed_count: int
    success_items: list[str]
    skipped_items: list[tuple[str, str]]  # (title, reason)
    failed_items: list[tuple[str, str]]  # (title, error)
