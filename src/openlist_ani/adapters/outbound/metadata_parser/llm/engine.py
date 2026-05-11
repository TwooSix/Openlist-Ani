"""LLM-backed title extraction strategy."""

from __future__ import annotations

from openlist_ani.application.anime_library_ingestion.models import ParseResult
from openlist_ani.integrations.llm import LLMClient

from .batch_parser import parse_title_batch_via_llm


class LLMTitleExtractEngine:
    """Parse release titles by delegating extraction to the configured LLM."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def parse_titles(self, titles: list[str]) -> list[ParseResult]:
        return await parse_title_batch_via_llm(self._llm, titles)
