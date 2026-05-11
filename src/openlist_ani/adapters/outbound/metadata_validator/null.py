"""No-op metadata validator."""

from __future__ import annotations

from openlist_ani.application.anime_library_ingestion.models import ParseResult


class NullMetadataValidator:
    """Validation strategy that accepts parser output as-is."""

    async def validate(self, results: list[ParseResult]) -> list[ParseResult]:
        return [result.model_copy(deep=True) for result in results]

    async def close(self) -> None:
        return None
