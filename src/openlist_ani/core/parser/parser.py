from cachetools import TTLCache

from ...config import config
from ...logger import logger
from ..website.model import AnimeResourceInfo
from .constants import DEFAULT_BATCH_SIZE
from .llm.batch_parser import parse_title_batch_via_llm
from .llm.client import OpenAILLMClient
from .model import ParseResult
from .tmdb.api import get_tmdb_client
from .tmdb.resolver import TMDBResolver

# Cache parsed results by title to avoid redundant LLM calls for
# entries that reappear after being skipped by priority filtering.
_parse_cache: TTLCache[str, ParseResult] = TTLCache(maxsize=1024, ttl=86400)


async def parse_metadata(
    entries: list[AnimeResourceInfo],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ParseResult]:
    if not config.llm.openai_api_key:
        logger.warning("OpenAI API key not set, skipping LLM extraction.")
        return [
            ParseResult(success=False, error="OpenAI API key not set") for _ in entries
        ]

    # Separate cached hits from misses.
    cached_results: dict[int, ParseResult] = {}
    to_parse: list[tuple[int, AnimeResourceInfo]] = []
    for i, entry in enumerate(entries):
        cached = _parse_cache.get(entry.title)
        if cached is not None:
            logger.debug(f"Parse cache hit: {entry.title}")
            cached_results[i] = cached
        else:
            to_parse.append((i, entry))

    if to_parse:
        logger.info(f"Parse cache: {len(cached_results)} hits, {len(to_parse)} misses")

    # Parse only cache-miss entries via LLM.
    parsed_map: dict[int, ParseResult] = {}
    if not to_parse:
        logger.info("All entries parsed from cache, skipping LLM.")
        return [cached_results[i] for i in range(len(entries))]

    miss_entries = [entry for _, entry in to_parse]
    miss_indices = [idx for idx, _ in to_parse]

    logger.info(
        f"Starting metadata parsing for {len(miss_entries)} entries "
        f"(batch_size={batch_size})"
    )

    llm = OpenAILLMClient(
        api_key=config.llm.openai_api_key,
        base_url=config.llm.openai_base_url,
        model=config.llm.openai_model,
    )
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

    for idx, pr in zip(miss_indices, fresh_results):
        parsed_map[idx] = pr
        # Cache successful results.
        title = entries[idx].title
        _parse_cache[title] = pr

    # Reassemble in original order.
    results: list[ParseResult] = []
    for i in range(len(entries)):
        if i in cached_results:
            results.append(cached_results[i])
        else:
            results.append(parsed_map[i])

    return results
