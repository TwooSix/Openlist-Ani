"""TMDB-backed anime identity resolver."""

from __future__ import annotations

import asyncio

from openlist_ani.application.anime_library_ingestion.models import (
    TMDBCandidate,
    TMDBMatch,
)

from ..constants import MAX_SEARCH_RESULTS_PER_QUERY, MAX_TMDB_QUERIES
from .api import TMDBClient
from .candidate import CandidateSelector, QueryExpander


class TMDBAnimeIdentityResolver:
    """Resolve parsed anime names by searching TMDB."""

    def __init__(
        self,
        *,
        tmdb_client: TMDBClient,
        query_expander: QueryExpander,
        candidate_selector: CandidateSelector,
    ) -> None:
        self._tmdb = tmdb_client
        self._query_expander = query_expander
        self._candidate_selector = candidate_selector

    async def close(self) -> None:
        await self._tmdb.close()

    async def resolve(self, anime_name: str) -> TMDBMatch | None:
        queries = await self._query_expander.expand(anime_name)
        if anime_name not in queries:
            queries.insert(0, anime_name)

        active_queries = queries[:MAX_TMDB_QUERIES]
        query_semaphore = asyncio.Semaphore(3)

        async def search_one(query: str) -> tuple[str, list[dict]]:
            async with query_semaphore:
                return query, await self._tmdb.search_tv_show(query)

        query_results = await asyncio.gather(
            *(search_one(query) for query in active_queries)
        )

        candidates = _deduplicate_candidates(query_results)
        if not candidates:
            return None
        return await self._candidate_selector.select(anime_name, candidates)


def _deduplicate_candidates(
    query_results: list[tuple[str, list[dict]]],
) -> list[TMDBCandidate]:
    dedup: dict[int, TMDBCandidate] = {}
    for _, search_results in query_results:
        for item in search_results[:MAX_SEARCH_RESULTS_PER_QUERY]:
            tmdb_id = item.get("id")
            if not tmdb_id or tmdb_id in dedup:
                continue
            dedup[tmdb_id] = TMDBCandidate(
                id=tmdb_id,
                name=item.get("name"),
                original_name=item.get("original_name"),
                first_air_date=item.get("first_air_date"),
                overview=(item.get("overview") or "")[:180],
                genre_ids=item.get("genre_ids", []),
                origin_country=item.get("origin_country", []),
            )
    return list(dedup.values())
