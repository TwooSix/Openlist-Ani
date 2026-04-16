from cachetools import TTLCache

from ...config import config
from ...logger import logger
from ..website.model import AnimeResourceInfo
from .constants import DEFAULT_BATCH_SIZE
from .llm.batch_parser import parse_title_batch_via_llm
from .llm.client import create_llm_client
from .model import ParseResult
from .tmdb.api import get_tmdb_client
from .tmdb.resolver import TMDBResolver

# Cache parsed results by title to avoid redundant LLM calls for
# entries that reappear after being skipped by priority filtering.
_parse_cache: TTLCache[str, ParseResult] = TTLCache(maxsize=1024, ttl=86400)


def _split_cached(
    entries: list[AnimeResourceInfo],
) -> tuple[dict[int, ParseResult], list[tuple[int, AnimeResourceInfo]]]:
    """Separate entries into cache hits and misses.

    Returns:
        A tuple of (cached_results dict, list of (index, entry) misses).
    """
    cached_results: dict[int, ParseResult] = {}
    to_parse: list[tuple[int, AnimeResourceInfo]] = []
    for i, entry in enumerate(entries):
        cached = _parse_cache.get(entry.title)
        if cached is not None:
            logger.debug(f"Parse cache hit: {entry.title}")
            cached_results[i] = cached.model_copy(deep=True)
        else:
            to_parse.append((i, entry))

    if to_parse:
        logger.info(f"Parse cache: {len(cached_results)} hits, {len(to_parse)} misses")

    return cached_results, to_parse


async def _parse_misses(
    miss_entries: list[AnimeResourceInfo],
    batch_size: int,
) -> list[ParseResult]:
    """Parse cache-miss entries via LLM in batches.

    Args:
        miss_entries: Entries that were not found in cache.
        batch_size: Number of entries per LLM batch call.

    Returns:
        A list of ParseResult for every miss entry, in order.
    """
    llm = create_llm_client(config.llm)
    tmdb_client = get_tmdb_client()
    resolver = TMDBResolver(llm_client=llm, tmdb_client=tmdb_client)

    total_chunks = (len(miss_entries) + batch_size - 1) // batch_size
    fresh_results: list[ParseResult] = []
    for chunk_start in range(0, len(miss_entries), batch_size):
        chunk = miss_entries[chunk_start : chunk_start + batch_size]
        chunk_idx = chunk_start // batch_size + 1
        titles = [e.title for e in chunk]
        logger.info(f"[{chunk_idx}/{total_chunks}] LLM parsing {len(chunk)} titles...")
        logger.debug(f"[{chunk_idx}/{total_chunks}] Titles: {titles}")
        parsed = await parse_title_batch_via_llm(llm, titles)
        for title, pr in zip(titles, parsed):
            pr.resource_title = title
        llm_ok = sum(1 for p in parsed if p.success)
        logger.info(
            f"[{chunk_idx}/{total_chunks}] LLM done: "
            f"{llm_ok}/{len(chunk)} succeeded, resolving TMDB..."
        )
        await resolver.resolve_and_validate(parsed)
        tmdb_ok = sum(1 for p in parsed if p.success)
        logger.info(
            f"[{chunk_idx}/{total_chunks}] TMDB resolved: "
            f"{tmdb_ok}/{len(chunk)} succeeded"
        )
        logger.debug(f"[{chunk_idx}/{total_chunks}] Results: {parsed}")
        fresh_results.extend(parsed)

    return fresh_results


async def parse_metadata(
    entries: list[AnimeResourceInfo],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ParseResult]:
    if not config.llm.openai_api_key:
        logger.warning("OpenAI API key not set, skipping LLM extraction.")
        return [
            ParseResult(success=False, error="OpenAI API key not set") for _ in entries
        ]

    cached_results, to_parse = _split_cached(entries)

    if not to_parse:
        logger.info("All entries parsed from cache, skipping LLM.")
        return [cached_results[i] for i in range(len(entries))]

    miss_entries = [entry for _, entry in to_parse]
    miss_indices = [idx for idx, _ in to_parse]

    logger.info(
        f"Starting metadata parsing for {len(miss_entries)} entries "
        f"(batch_size={batch_size})"
    )

    fresh_results = await _parse_misses(miss_entries, batch_size)

    # Map fresh results back and update cache.
    parsed_map: dict[int, ParseResult] = {}
    for idx, pr in zip(miss_indices, fresh_results):
        parsed_map[idx] = pr
        if pr.success:
            _parse_cache[entries[idx].title] = pr.model_copy(deep=True)

    # Reassemble in original order.
    return [
        cached_results[i] if i in cached_results else parsed_map[i]
        for i in range(len(entries))
    ]
