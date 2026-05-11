"""Metadata parser engine strategy interface."""

from __future__ import annotations

from typing import Protocol

from openlist_ani.application.anime_library_ingestion.models import ParseResult


class MetadataParserEngine(Protocol):
    """Strategy interface for release title metadata extraction."""

    async def parse_titles(self, titles: list[str]) -> list[ParseResult]:
        """Parse release titles into preliminary metadata results."""
        ...
