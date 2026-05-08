"""
Metadata-based blacklist filter.

Filters out RSS entries whose LLM-parsed metadata (fansub, quality,
languages) matches any value in the user-configured exclusion lists
under ``[rss.filter]``.
"""

from __future__ import annotations

import asyncio

from openlist_ani.application.anime_library_ingestion.settings import (
    MetadataFilterSettings,
)
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.logger import logger


class MetadataFilter:
    """Filter candidates by metadata blacklists."""

    def __init__(self, settings: MetadataFilterSettings) -> None:
        self._settings = settings

    async def apply(
        self,
        candidates: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Return candidates not matching any metadata exclusion rule.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed metadata filtering.
        """
        await asyncio.sleep(0)
        if not candidates:
            return []

        filter_cfg = self._settings
        if (
            not filter_cfg.exclude_fansub
            and not filter_cfg.exclude_quality
            and not filter_cfg.exclude_languages
        ):
            return candidates

        accepted: list[AnimeRelease] = []
        for candidate in candidates:
            reason = self._exclusion_reason(candidate, filter_cfg)
            if reason:
                logger.debug(f"Metadata filter: excluding {candidate.title} ({reason})")
                continue
            accepted.append(candidate)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.debug(
                f"Metadata filter: {len(accepted)} accepted, {skipped} excluded"
            )
        return accepted

    @staticmethod
    def _exclusion_reason(
        candidate: AnimeRelease,
        filter_cfg: MetadataFilterSettings,
    ) -> str | None:
        """Return the matched exclusion rule, if any."""
        # Fansub: exact match
        if filter_cfg.exclude_fansub and candidate.fansub in filter_cfg.exclude_fansub:
            return f"fansub={candidate.fansub}"

        # Quality: match against .value string
        if (
            filter_cfg.exclude_quality
            and candidate.quality
            and candidate.quality.value in filter_cfg.exclude_quality
        ):
            return f"quality={candidate.quality.value}"

        # Languages: any language in exclusion list
        if filter_cfg.exclude_languages:
            for lang in candidate.languages:
                if lang.value in filter_cfg.exclude_languages:
                    return f"language={lang.value}"

        return None
