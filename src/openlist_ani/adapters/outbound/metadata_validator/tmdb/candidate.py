"""TMDB query expansion and candidate selection strategies."""

from __future__ import annotations

import json
import re
import asyncio
from difflib import SequenceMatcher
from typing import Protocol

from openlist_ani.application.anime_library_ingestion.models import (
    TMDBCandidate,
    TMDBMatch,
)
from openlist_ani.integrations.llm import LLMClient, parse_json_from_markdown
from openlist_ani.logger import logger

from ..constants import MAX_TMDB_QUERIES
from .prompts import QUERY_EXPANSION_SYSTEM_PROMPT, TMDB_SELECTION_SYSTEM_PROMPT


class QueryExpander(Protocol):
    async def expand(self, anime_name: str) -> list[str]:
        """Return TMDB search queries for a parsed anime name."""
        ...


class StaticQueryExpander:
    """Deterministic query expander for non-LLM parsers.

    General case: search the parsed title unchanged.
    Corner cases: add a few title-visible variants that frequently affect TMDB
    search but can be handled without semantic inference or an LLM.
    """

    async def expand(self, anime_name: str) -> list[str]:
        await asyncio.sleep(0)
        return _static_query_variants(anime_name)


class LLMQueryExpander:
    """Use LLM to generate alternative TMDB search queries."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def expand(self, anime_name: str) -> list[str]:
        messages = [
            {"role": "system", "content": QUERY_EXPANSION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"anime_name": anime_name}, ensure_ascii=False),
            },
        ]
        try:
            content = await self._llm.complete_chat(messages)
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
            if anime_name not in normalized:
                normalized.insert(0, anime_name)
            return normalized[:MAX_TMDB_QUERIES] if normalized else [anime_name]
        except Exception as e:
            logger.debug(f"Failed to expand TMDB queries for {anime_name}: {e}")
            return [anime_name]


class CandidateSelector(Protocol):
    async def select(
        self,
        anime_name: str,
        candidates: list[TMDBCandidate],
    ) -> TMDBMatch | None:
        """Select the best candidate or return None."""
        ...


class HeuristicCandidateSelector:
    """Select TMDB candidates with deterministic string matching."""

    async def select(
        self,
        anime_name: str,
        candidates: list[TMDBCandidate],
    ) -> TMDBMatch | None:
        await asyncio.sleep(0)
        if not candidates:
            return None
        ranked = sorted(
            candidates,
            key=lambda candidate: _candidate_score(anime_name, candidate),
            reverse=True,
        )
        selected = ranked[0]
        logger.debug(
            f"TMDB heuristic candidate selected for '{anime_name}': "
            f"{selected.name or selected.original_name or selected.id}"
        )
        return TMDBMatch(
            tmdb_id=selected.id,
            anime_name=_select_output_name(anime_name, selected),
            confidence="heuristic",
        )


class LLMCandidateSelector:
    """Use LLM to select a TMDB candidate, with deterministic fallback."""

    def __init__(
        self,
        llm_client: LLMClient,
        fallback: CandidateSelector | None = None,
    ) -> None:
        self._llm = llm_client
        self._fallback = fallback or HeuristicCandidateSelector()

    async def select(
        self,
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
                        "candidates": [
                            candidate.model_dump() for candidate in candidates
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            content = await self._llm.complete_chat(messages)
            payload = parse_json_from_markdown(content)
            if not payload:
                return await self._fallback.select(anime_name, candidates)
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                return await self._fallback.select(anime_name, candidates)
            tmdb_id = parsed.get("tmdb_id")
            if tmdb_id is None:
                return await self._fallback.select(anime_name, candidates)
            candidate_map = {candidate.id: candidate for candidate in candidates}
            if tmdb_id not in candidate_map:
                return await self._fallback.select(anime_name, candidates)
            selected = candidate_map[tmdb_id]
            return TMDBMatch(
                tmdb_id=tmdb_id,
                anime_name=_select_output_name(anime_name, selected),
                confidence=parsed.get("confidence", "unknown"),
            )
        except Exception as e:
            logger.debug(f"Failed to select TMDB candidate for {anime_name}: {e}")
            return await self._fallback.select(anime_name, candidates)


def _candidate_score(anime_name: str, candidate: TMDBCandidate) -> float:
    target = _normalize_candidate_name(anime_name)
    names = [
        _normalize_candidate_name(name)
        for name in (candidate.name, candidate.original_name)
        if name
    ]
    if not target or not names:
        return 0.0

    best = 0.0
    for name in names:
        if name == target:
            best = max(best, 100.0)
        elif target in name or name in target:
            length_ratio = min(len(target), len(name)) / max(len(target), len(name))
            best = max(best, 85.0 + length_ratio)
        else:
            best = max(best, SequenceMatcher(a=target, b=name).ratio() * 80.0)
    return best


def _normalize_candidate_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _static_query_variants(anime_name: str) -> list[str]:
    variants = [anime_name]
    normalized = anime_name.replace("／", "/")
    _append_unique(variants, normalized)

    # Corner case: very long Chinese subtitles often differ by one or two words
    # between RSS title text and TMDB. Searching the main title before "～"
    # resolves cases such as "安逸领主的愉快领地防卫～用生产系魔术...".
    if "～" in anime_name:
        _append_unique(variants, anime_name.split("～", 1)[0])

    # Corner case: some ANi titles omit punctuation in Fate franchise aliases,
    # e.g. "Fatestrange Fake" while TMDB indexes "Fate/strange Fake".
    fate_spaced = re.sub(r"(?i)^Fate(?=[A-Z])", "Fate ", anime_name)
    _append_unique(variants, fate_spaced)
    _append_unique(variants, fate_spaced.replace("Fate ", "Fate/", 1))

    # Corner case: "他国日记" is a visible Chinese alias for TMDB "异国日记".
    # A broad "日记" query is too noisy, but the suffix remains selective.
    if anime_name.endswith("国日记") and len(anime_name) >= 4:
        _append_unique(variants, anime_name[1:])
        _append_unique(variants, "異国日記")
        _append_unique(variants, "違国日記")

    return variants[:MAX_TMDB_QUERIES]


def _append_unique(values: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in values:
        values.append(value)


def _select_output_name(anime_name: str, candidate: TMDBCandidate) -> str:
    """Choose the name written back after TMDB identity validation.

    General case: use the authoritative localized TMDB title. That corrects
    common title-visible aliases such as regional translations or romaji-only
    release names.

    Corner case: TMDB and the parsed title can differ only by punctuation, e.g.
    "妻子变成小学生。" vs "妻子变成小学生" or ASCII hyphen vs em dash. In those
    cases the release title already carries the user's preferred naming style,
    so preserving it avoids needless churn while still attaching the TMDB id.
    Whitespace-only differences still prefer TMDB because historical outputs
    consistently collapse spaces in mixed CJK/Latin titles.
    """
    parsed_name = _normalize_output_punctuation(anime_name)
    selected_name = _normalize_output_punctuation(
        candidate.name or candidate.original_name or parsed_name
    )
    if _normalize_candidate_name(parsed_name) != _normalize_candidate_name(
        selected_name
    ):
        return selected_name

    if _remove_spaces(parsed_name) == _remove_spaces(selected_name):
        return selected_name
    return parsed_name


def _remove_spaces(value: str) -> str:
    return "".join(ch for ch in value if not ch.isspace())


def _normalize_output_punctuation(value: str) -> str:
    return (
        value.replace("—", "-")
        .replace("–", "-")
        .replace("－", "-")
        .replace("「", "“")
        .replace("」", "”")
    )
