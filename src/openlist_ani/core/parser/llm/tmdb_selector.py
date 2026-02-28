import json

from ....logger import logger
from ..constants import MAX_TMDB_QUERIES
from ..model import TMDBCandidate, TMDBMatch
from ..prompts import QUERY_EXPANSION_SYSTEM_PROMPT, TMDB_SELECTION_SYSTEM_PROMPT
from ..utils import parse_json_from_markdown
from .client import LLMClient


async def generate_tmdb_queries(llm: LLMClient, anime_name: str) -> list[str]:
    messages = [
        {"role": "system", "content": QUERY_EXPANSION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps({"anime_name": anime_name}, ensure_ascii=False),
        },
    ]
    try:
        content = await llm.complete_chat(messages)
        payload = parse_json_from_markdown(content)
        if not payload:
            return [anime_name]
        parsed = json.loads(payload)
        queries = parsed.get("queries", []) if isinstance(parsed, dict) else []
        normalized: list[str] = []
        for query in queries:
            if not isinstance(query, str):
                continue
            query = query.strip()
            if query and query not in normalized:
                normalized.append(query)
        return normalized[:MAX_TMDB_QUERIES] if normalized else [anime_name]
    except Exception as e:
        logger.debug(f"Failed to expand TMDB queries for {anime_name}: {e}")
        return [anime_name]


async def select_tmdb_candidate(
    llm: LLMClient,
    anime_name: str,
    candidates: list[TMDBCandidate],
) -> TMDBMatch | None:
    messages = [
        {"role": "system", "content": TMDB_SELECTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "anime_name": anime_name,
                    "candidates": [c.model_dump() for c in candidates],
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        content = await llm.complete_chat(messages)
        payload = parse_json_from_markdown(content)
        if not payload:
            return None
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            return None
        tmdb_id = parsed.get("tmdb_id")
        if tmdb_id is None:
            return None
        candidate_map = {c.id: c for c in candidates}
        if tmdb_id not in candidate_map:
            return None
        selected = candidate_map[tmdb_id]
        return TMDBMatch(
            tmdb_id=tmdb_id,
            anime_name=selected.name or parsed.get("anime_name") or anime_name,
            confidence=parsed.get("confidence", "unknown"),
        )
    except Exception as e:
        logger.debug(f"Failed to select TMDB candidate for {anime_name}: {e}")
        return None
