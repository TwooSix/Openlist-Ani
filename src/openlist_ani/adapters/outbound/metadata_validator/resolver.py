"""Validation-stage strategy interfaces."""

from __future__ import annotations

from typing import Protocol

from openlist_ani.application.anime_library_ingestion.models import (
    EpisodeMapping,
    TMDBMatch,
)


class AnimeIdentityResolver(Protocol):
    """Resolve a parsed anime name to an authoritative identity."""

    async def resolve(self, anime_name: str) -> TMDBMatch | None:
        """Return the best authoritative match for a parsed anime name."""
        ...

    async def close(self) -> None:
        """Release resources held by the resolver."""
        ...


class EpisodeValidator(Protocol):
    """Validate and map parsed season/episode against an authoritative source."""

    async def validate(
        self,
        *,
        tmdb_id: int,
        season: int,
        episode: int,
        anime_name: str,
        release_title: str,
    ) -> EpisodeMapping | None:
        """Return authoritative season/episode mapping or None if invalid."""
        ...
