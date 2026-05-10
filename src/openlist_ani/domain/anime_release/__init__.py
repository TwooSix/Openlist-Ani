"""Anime release domain models."""

from .collection import detect_collection
from .model import AnimeRelease, LanguageType, VideoQuality
from .naming import (
    ReleaseDirectoryPlanner,
    ReleaseFilenamePlanner,
    format_anime_episode,
    format_release_stem,
    release_anime_name,
    release_episode,
    release_season,
    sanitize_filename,
)

__all__ = [
    "AnimeRelease",
    "LanguageType",
    "ReleaseDirectoryPlanner",
    "ReleaseFilenamePlanner",
    "VideoQuality",
    "detect_collection",
    "format_anime_episode",
    "format_release_stem",
    "release_anime_name",
    "release_episode",
    "release_season",
    "sanitize_filename",
]
