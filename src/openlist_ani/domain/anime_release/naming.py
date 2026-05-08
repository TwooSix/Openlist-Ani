"""Filename and display-name rules for anime releases."""

from __future__ import annotations

import os
import re

from .model import AnimeRelease


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in common filesystems."""
    return re.sub(r'[<>:"/\\|?*]', " ", name).strip()


def format_anime_episode(
    anime_name: str | None, season: int | None, episode: int | None
) -> str:
    """Safely format anime episode info for logs."""
    name = anime_name or "Unknown"
    season_str = f"S{season:02d}" if season is not None else "S??"
    episode_str = f"E{episode:02d}" if episode is not None else "E??"
    return f"{name} {season_str}{episode_str}"


def release_anime_name(release: AnimeRelease) -> str:
    return sanitize_filename(release.anime_name or "Unknown")


def release_season(release: AnimeRelease) -> int:
    return release.season or 1


def release_episode(release: AnimeRelease) -> int:
    return release.episode or 1


def format_release_stem(
    rename_format: str,
    *,
    anime_name: str | None,
    season: int | None,
    episode: int | None,
    fansub: str | None = None,
    quality: object | None = None,
    languages: object | None = None,
    version: int = 1,
    include_version: bool = True,
    **extra_context: object,
) -> str:
    """Build a rename stem from raw metadata without constructing a release."""
    context = dict(extra_context)
    context["anime_name"] = sanitize_filename(anime_name or "Unknown")
    context["season"] = season or 1
    context["episode"] = episode or 1
    context["fansub"] = fansub or ""
    context["quality"] = str(quality) if quality is not None else ""
    context["languages"] = _format_languages(languages)
    version = version or 1

    try:
        stem = rename_format.format(**context).strip()
    except (KeyError, ValueError, IndexError, TypeError):
        stem = (
            f"{context['anime_name']} S{context['season']:02d}E{context['episode']:02d}"
        )

    if include_version and version > 1:
        stem = f"{stem} v{version}"

    return stem


def _format_languages(languages: object | None) -> str:
    if languages is None:
        return ""
    if isinstance(languages, list):
        return "".join(str(lang) for lang in languages)
    return str(languages)


class ReleaseFilenamePlanner:
    """Build final filenames from release metadata and a configured format."""

    def __init__(self, rename_format: str) -> None:
        self._rename_format = rename_format

    def filename(self, release: AnimeRelease, source_filename: str) -> str:
        return f"{self.stem(release)}{self._extension(source_filename)}".strip()

    def stem(self, release: AnimeRelease, include_version: bool = True) -> str:
        rename_context = vars(release).copy()
        rename_context.pop("title", None)
        return format_release_stem(
            self._rename_format,
            include_version=include_version,
            **rename_context,
        )

    @staticmethod
    def _extension(source_filename: str) -> str:
        _, ext = os.path.splitext(source_filename)
        return ext or ".mp4"


class ReleaseDirectoryPlanner:
    """Build target library directories for downloaded releases."""

    def target_directory_path(self, base_path: str, release: AnimeRelease) -> str:
        season_dir = f"Season {release_season(release)}"
        return f"{base_path.rstrip('/')}/{release_anime_name(release)}/{season_dir}"
