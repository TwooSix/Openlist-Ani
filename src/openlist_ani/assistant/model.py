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
