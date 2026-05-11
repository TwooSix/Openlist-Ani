"""TMDB-backed episode validation."""

from __future__ import annotations

from openlist_ani.application.anime_library_ingestion.models import (
    EpisodeMapping,
    SeasonInfo,
)
from openlist_ani.integrations.llm import LLMClient
from openlist_ani.logger import logger

from .api import TMDBClient
from .episode_mapper import EpisodeMapper, MappingContext


class TMDBEpisodeValidator:
    """Validate and map parsed season/episode values against TMDB."""

    def __init__(
        self,
        *,
        tmdb_client: TMDBClient,
        llm_client: LLMClient | None = None,
        episode_mapper: EpisodeMapper | None = None,
    ) -> None:
        self._tmdb = tmdb_client
        self._llm = llm_client
        self._mapper = episode_mapper or EpisodeMapper()

    async def validate(
        self,
        *,
        tmdb_id: int,
        season: int,
        episode: int,
        anime_name: str,
        release_title: str,
    ) -> EpisodeMapping | None:
        details = await self._tmdb.get_tv_show_details(tmdb_id)
        if not details:
            logger.warning(
                f"TMDB details unavailable for id={tmdb_id}, cannot verify "
                f"S{season:02d}E{episode:02d} ({anime_name})"
            )
            return None

        sorted_seasons = SeasonInfo.from_raw_list(details.get("seasons", []))
        logger.debug(
            f"TMDB verify: {anime_name} S{season:02d}E{episode:02d} | "
            f"tmdb_id={tmdb_id} | TMDB seasons: "
            f"{[(s.season_number, s.episode_count) for s in sorted_seasons]} | "
            f"target_season={'found' if any(s.season_number == season for s in sorted_seasons) else 'NOT found'}"
        )

        mapping = await self._mapper.map(
            MappingContext(
                tmdb_id=tmdb_id,
                fansub_season=season,
                fansub_episode=episode,
                sorted_seasons=sorted_seasons,
                tmdb_client=self._tmdb,
                release_title=release_title,
                llm_client=self._llm,
            )
        )
        if mapping:
            if mapping.strategy not in ("direct", "special_passthrough"):
                logger.debug(
                    f"{mapping.strategy.replace('_', ' ').title()} mapping: "
                    f"{anime_name} S{season:02d}E{episode:02d} -> "
                    f"S{mapping.season:02d}E{mapping.episode:02d}"
                )
            return mapping

        logger.warning(
            f"TMDB mapping failed: {anime_name} S{season:02d}E{episode:02d} "
            f"(tmdb_id={tmdb_id}) - no strategy could map to a valid TMDB season/episode"
        )
        return None
