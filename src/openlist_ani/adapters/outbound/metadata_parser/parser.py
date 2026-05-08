"""Metadata parser facade."""

from __future__ import annotations

from cachetools import TTLCache

from openlist_ani.application.anime_library_ingestion.models import ParseResult
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.logger import logger

from .constants import DEFAULT_BATCH_SIZE
from .llm.batch_parser import parse_title_batch_via_llm
from .llm.client import LLMClient, create_llm_client
from .settings import MetadataParserSettings
from .tmdb.api import get_tmdb_client
from .tmdb.resolver import TMDBResolver


class ParseCache:
    """TTL cache for parsed release-title metadata."""

    def __init__(self, maxsize: int = 1024, ttl: int = 86400) -> None:
        self._items: TTLCache[str, ParseResult] = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, title: str) -> ParseResult | None:
        cached = self._items.get(title)
        return cached.model_copy(deep=True) if cached is not None else None

    def put(self, title: str, result: ParseResult) -> None:
        self._items[title] = result.model_copy(deep=True)


class MetadataParserAdapter:
    """Facade for LLM title parsing plus TMDB validation."""

    def __init__(
        self,
        llm_client: LLMClient | None,
        tmdb_resolver: TMDBResolver | None,
        cache: ParseCache | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        disabled_reason: str | None = None,
    ) -> None:
        self._llm = llm_client
        self._tmdb_resolver = tmdb_resolver
        self._cache = cache or ParseCache()
        self._batch_size = batch_size
        self._disabled_reason = disabled_reason

    @classmethod
    def from_settings(cls, settings: MetadataParserSettings) -> MetadataParserAdapter:
        if not settings.api_key:
            return cls(
                llm_client=None,
                tmdb_resolver=None,
                disabled_reason="OpenAI API key not set",
            )

        llm = create_llm_client(settings)
        tmdb_client = get_tmdb_client(
            api_key=settings.tmdb_api_key,
            language=settings.tmdb_language,
        )
        return cls(
            llm_client=llm,
            tmdb_resolver=TMDBResolver(llm_client=llm, tmdb_client=tmdb_client),
        )

    async def parse(self, entries: list[AnimeRelease]) -> list[ParseResult]:
        if self._disabled_reason:
            logger.warning(f"{self._disabled_reason}, skipping LLM extraction.")
            return [
                ParseResult(success=False, error=self._disabled_reason) for _ in entries
            ]

        cached_results, to_parse = self._split_cached(entries)
        if not to_parse:
            logger.debug("All entries parsed from cache, skipping LLM")
            return [cached_results[i] for i in range(len(entries))]

        miss_entries = [entry for _, entry in to_parse]
        miss_indices = [idx for idx, _ in to_parse]

        logger.debug(
            f"Starting metadata parsing for {len(miss_entries)} entries "
            f"(batch_size={self._batch_size})"
        )
        fresh_results = await self._parse_misses(miss_entries)

        parsed_map: dict[int, ParseResult] = {}
        for idx, result in zip(miss_indices, fresh_results):
            parsed_map[idx] = result
            if result.success:
                self._cache.put(entries[idx].title, result)

        return [
            cached_results[i] if i in cached_results else parsed_map[i]
            for i in range(len(entries))
        ]

    async def close(self) -> None:
        if self._tmdb_resolver is not None:
            await self._tmdb_resolver.close()

    def _split_cached(
        self,
        entries: list[AnimeRelease],
    ) -> tuple[dict[int, ParseResult], list[tuple[int, AnimeRelease]]]:
        cached_results: dict[int, ParseResult] = {}
        to_parse: list[tuple[int, AnimeRelease]] = []

        for i, entry in enumerate(entries):
            cached = self._cache.get(entry.title)
            if cached is not None:
                logger.debug(f"Parse cache hit: {entry.title}")
                cached_results[i] = cached
            else:
                to_parse.append((i, entry))

        if to_parse:
            logger.debug(
                f"Parse cache: {len(cached_results)} hits, {len(to_parse)} misses"
            )

        return cached_results, to_parse

    async def _parse_misses(
        self, miss_entries: list[AnimeRelease]
    ) -> list[ParseResult]:
        if self._llm is None or self._tmdb_resolver is None:
            reason = self._disabled_reason or "Metadata parser is not configured"
            return [ParseResult(success=False, error=reason) for _ in miss_entries]

        total_chunks = (len(miss_entries) + self._batch_size - 1) // self._batch_size
        fresh_results: list[ParseResult] = []
        for chunk_start in range(0, len(miss_entries), self._batch_size):
            chunk = miss_entries[chunk_start : chunk_start + self._batch_size]
            chunk_idx = chunk_start // self._batch_size + 1
            titles = [entry.title for entry in chunk]
            logger.debug(
                f"[{chunk_idx}/{total_chunks}] LLM parsing {len(chunk)} titles..."
            )
            logger.debug(
                f"[{chunk_idx}/{total_chunks}] Title sample: {_sample_titles(titles)}"
            )
            parsed = await parse_title_batch_via_llm(self._llm, titles)
            for title, result in zip(titles, parsed):
                result.release_title = title

            llm_ok = sum(1 for result in parsed if result.success)
            logger.debug(
                f"[{chunk_idx}/{total_chunks}] LLM done: "
                f"{llm_ok}/{len(chunk)} succeeded, resolving TMDB..."
            )
            await self._tmdb_resolver.resolve_and_validate(parsed)

            tmdb_ok = sum(1 for result in parsed if result.success)
            logger.debug(
                f"[{chunk_idx}/{total_chunks}] TMDB resolved: "
                f"{tmdb_ok}/{len(chunk)} succeeded"
            )
            logger.debug(
                f"[{chunk_idx}/{total_chunks}] Failed parse sample: "
                f"{_sample_failures(parsed)}"
            )
            fresh_results.extend(parsed)

        return fresh_results


def _sample_titles(titles: list[str], limit: int = 3) -> list[str]:
    sample = titles[:limit]
    if len(titles) > limit:
        sample.append(f"... {len(titles) - limit} more")
    return sample


def _sample_failures(results: list[ParseResult], limit: int = 3) -> list[str]:
    failures = [
        f"{result.release_title or '<unknown>'}: {result.error or 'unknown error'}"
        for result in results
        if not result.success
    ]
    sample = failures[:limit]
    if len(failures) > limit:
        sample.append(f"... {len(failures) - limit} more")
    return sample
