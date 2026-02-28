import asyncio

from ....logger import logger
from ..constants import (
    MAX_SEARCH_RESULTS_PER_QUERY,
    MAX_TMDB_QUERIES,
    TMDB_RESOLVE_CONCURRENCY,
)
from ..llm.client import LLMClient
from ..llm.tmdb_selector import generate_tmdb_queries, select_tmdb_candidate
from ..model import (
    EpisodeMapping,
    ParseResult,
    ResourceTitleParseResult,
    SeasonInfo,
    TMDBCandidate,
    TMDBMatch,
)
from ..tmdb.api import TMDBClient
from .episode_mapper import EpisodeMapper, MappingContext

_CACHE_MISS = object()


class _VerifyCache:
    def __init__(self) -> None:
        self._store: dict[tuple[int, int, int], EpisodeMapping | None] = {}

    def get(self, key: tuple[int, int, int]) -> EpisodeMapping | None | object:
        return self._store.get(key, _CACHE_MISS)

    def put(self, key: tuple[int, int, int], value: EpisodeMapping | None) -> None:
        self._store[key] = value


class TMDBResolver:
    def __init__(
        self,
        llm_client: LLMClient,
        tmdb_client: TMDBClient,
        episode_mapper: EpisodeMapper | None = None,
        max_concurrency: int = TMDB_RESOLVE_CONCURRENCY,
    ) -> None:
        self._llm = llm_client
        self._tmdb = tmdb_client
        self._mapper = episode_mapper or EpisodeMapper()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def resolve_and_validate(self, results: list[ParseResult]) -> None:
        """Resolve TMDB IDs and validate episodes for parse results in-place."""
        successful_items = [r for r in results if r.success and r.result]
        if not successful_items:
            return

        unique_names = {
            item.result.anime_name.strip()
            for item in successful_items
            if item.result and item.result.anime_name.strip()
        }

        # Get {llm_anime_name: {tmdb_id, tmdb_anime_name}}.
        resolved_map = await self._resolve_batch(unique_names)
        verify_cache = _VerifyCache()

        for item in successful_items:
            await self._process_single_item(item, resolved_map, verify_cache)

    async def _process_single_item(
        self,
        item: ParseResult,
        resolved_map: dict[str, TMDBMatch],
        verify_cache: _VerifyCache,
    ) -> None:
        """Process a single parse result: resolve TMDB ID and verify episode."""
        parse_result = item.result
        if not parse_result:
            return

        original_name = parse_result.anime_name.strip()
        parse_result.tmdb_id = None

        resolved = resolved_map.get(original_name)
        if resolved:
            parse_result.tmdb_id = resolved.tmdb_id
            parse_result.anime_name = resolved.anime_name

        if not parse_result.tmdb_id:
            logger.debug(
                f"TMDB unresolved for parsed anime '{original_name}', marking parse as failed"
            )
            item.success = False
            item.error = "TMDB match not found for parsed anime name"
            item.result = None
            return

        mapping = await self._get_or_verify_mapping(item, parse_result, verify_cache)

        if mapping is None:
            logger.debug(
                f"TMDB season/episode mapping failed for '{parse_result.anime_name}' "
                f"S{parse_result.season:02d}E{parse_result.episode:02d}, marking as failed"
            )
            item.success = False
            item.error = (
                f"TMDB season/episode mapping failed: "
                f"S{parse_result.season:02d}E{parse_result.episode:02d} "
                f"has no authoritative match in TMDB (id={parse_result.tmdb_id})"
            )
            item.result = None
        else:
            parse_result.season = mapping.season
            parse_result.episode = mapping.episode

    async def _get_or_verify_mapping(
        self,
        item: ParseResult,
        parse_result: ResourceTitleParseResult,
        verify_cache: _VerifyCache,
    ) -> EpisodeMapping | None:
        """Get cached mapping or verify episode against TMDB."""
        verify_key = (
            parse_result.tmdb_id,
            parse_result.season,
            parse_result.episode,
        )
        cached = verify_cache.get(verify_key)
        if cached is not _CACHE_MISS:
            return cached  # type: ignore[return-value]

        # auto align season/episode to TMDB.
        mapping = await self._verify_episode(
            tmdb_id=parse_result.tmdb_id,
            season=parse_result.season,
            episode=parse_result.episode,
            anime_name=parse_result.anime_name,
            resource_title=item.resource_title or "",
        )
        verify_cache.put(verify_key, mapping)
        return mapping

    async def resolve_tmdb_id(self, anime_name: str) -> TMDBMatch | None:
        queries = await generate_tmdb_queries(self._llm, anime_name)
        if anime_name not in queries:
            queries.insert(0, anime_name)

        active_queries = queries[:MAX_TMDB_QUERIES]
        query_semaphore = asyncio.Semaphore(3)

        async def _search_one(query: str) -> tuple[str, list[dict]]:
            async with query_semaphore:
                return query, await self._tmdb.search_tv_show(query)

        query_results = await asyncio.gather(
            *[_search_one(query) for query in active_queries]
        )

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

        candidates = list(dedup.values())
        if not candidates:
            return None

        selected = await select_tmdb_candidate(self._llm, anime_name, candidates)
        if selected:
            return selected

        logger.debug(
            f"TMDB candidate selection returned empty for '{anime_name}', "
            f"using top TMDB candidate fallback"
        )
        fallback = candidates[0]
        return TMDBMatch(
            tmdb_id=fallback.id,
            anime_name=fallback.name or fallback.original_name or anime_name,
        )

    async def _resolve_batch(self, names: set[str]) -> dict[str, TMDBMatch]:
        if not names:
            return {}

        async def _resolve_one(name: str) -> tuple[str, TMDBMatch | None]:
            async with self._semaphore:
                resolved = await self.resolve_tmdb_id(name)
                return name, resolved

        pairs = await asyncio.gather(*[_resolve_one(name) for name in names])
        return {name: resolved for name, resolved in pairs if resolved is not None}

    async def _verify_episode(
        self,
        tmdb_id: int,
        season: int,
        episode: int,
        anime_name: str | None = None,
        resource_title: str = "",
    ) -> EpisodeMapping | None:
        details = await self._tmdb.get_tv_show_details(tmdb_id)
        if not details:
            logger.warning(
                f"TMDB details unavailable for id={tmdb_id}, cannot verify "
                f"S{season:02d}E{episode:02d} ({anime_name or ''})"
            )
            return None

        raw_seasons = details.get("seasons", [])
        sorted_seasons = SeasonInfo.from_raw_list(raw_seasons)

        logger.debug(
            f"TMDB verify: {anime_name or ''} S{season:02d}E{episode:02d} | "
            f"tmdb_id={tmdb_id} | TMDB seasons: "
            f"{[(s.season_number, s.episode_count) for s in sorted_seasons]} | "
            f"target_season={'found' if any(s.season_number == season for s in sorted_seasons) else 'NOT found'}"
        )

        ctx = MappingContext(
            tmdb_id=tmdb_id,
            fansub_season=season,
            fansub_episode=episode,
            sorted_seasons=sorted_seasons,
            tmdb_client=self._tmdb,
            resource_title=resource_title,
            llm_client=self._llm,
        )
        mapping = await self._mapper.map(ctx)
        if mapping:
            if mapping.strategy not in ("direct", "special_passthrough"):
                logger.debug(
                    f"{mapping.strategy.replace('_', ' ').title()} mapping: "
                    f"{anime_name or ''} S{season:02d}E{episode:02d} → "
                    f"S{mapping.season:02d}E{mapping.episode:02d}"
                )
            return mapping

        logger.warning(
            f"TMDB mapping failed: {anime_name or ''} S{season:02d}E{episode:02d} "
            f"(tmdb_id={tmdb_id}) — no strategy could map to a valid TMDB season/episode"
        )
        return None
