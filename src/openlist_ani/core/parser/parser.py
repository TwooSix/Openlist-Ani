from ...config import config
from ...logger import logger
from ..website.model import AnimeResourceInfo
from .constants import DEFAULT_BATCH_SIZE
from .llm.batch_parser import parse_title_batch_via_llm
from .llm.client import OpenAILLMClient
from .model import ParseResult
from .tmdb.api import get_tmdb_client
from .tmdb.resolver import TMDBResolver


async def parse_metadata(
    entries: list[AnimeResourceInfo],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ParseResult]:
    if not config.llm.openai_api_key:
        logger.warning("OpenAI API key not set, skipping LLM extraction.")
        return [
            ParseResult(success=False, error="OpenAI API key not set") for _ in entries
        ]

    llm = OpenAILLMClient(
        api_key=config.llm.openai_api_key,
        base_url=config.llm.openai_base_url,
        model=config.llm.openai_model,
    )
    tmdb_client = get_tmdb_client()
    resolver = TMDBResolver(llm_client=llm, tmdb_client=tmdb_client)

    results: list[ParseResult] = []
    for chunk_start in range(0, len(entries), batch_size):
        chunk = entries[chunk_start : chunk_start + batch_size]
        titles = [e.title for e in chunk]
        parsed = await parse_title_batch_via_llm(llm, titles)
        for title, pr in zip(titles, parsed):
            pr.resource_title = title
        enriched = await resolver.resolve_and_validate(parsed)
        results.extend(enriched)

    return results
