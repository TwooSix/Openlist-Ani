"""ParseResult correction helpers for metadata validation."""

from __future__ import annotations

from openlist_ani.application.anime_library_ingestion.models import (
    EpisodeMapping,
    ParseResult,
    TMDBMatch,
)


class ParseResultCorrector:
    """Apply authoritative metadata back to ParseResult objects."""

    def apply_identity(self, item: ParseResult, identity: TMDBMatch) -> None:
        if item.result is None:
            return
        item.result.tmdb_id = identity.tmdb_id
        item.result.anime_name = identity.anime_name

    def apply_episode_mapping(self, item: ParseResult, mapping: EpisodeMapping) -> None:
        if item.result is None:
            return
        item.result.season = mapping.season
        item.result.episode = mapping.episode

    def fail_identity(self, item: ParseResult) -> None:
        item.success = False
        item.error = "TMDB match not found for parsed anime name"
        item.result = None

    def fail_episode(self, item: ParseResult, *, season: int, episode: int) -> None:
        item.success = False
        item.error = (
            f"TMDB season/episode mapping failed: "
            f"S{season:02d}E{episode:02d} has no authoritative match"
        )
        item.result = None
