"""
Regex-based title exclusion filter.

Filters out RSS entries whose title matches any of the user-configured
regular expression patterns in ``config.rss.filter.exclude_patterns``,
plus a built-in blacklist for collection-style titles.
"""

from __future__ import annotations

import asyncio
import re

from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.logger import logger

# 现阶段不支持合集下载，默认过滤
_DEFAULT_EXCLUDE_PATTERNS = [r"(\d{2}-\d{2}|合集)"]


class RegexTitleFilter:
    """Filter candidates by matching their title against exclusion patterns.

    A built-in pattern excludes titles that look like collections.
    """

    def __init__(self, exclude_patterns: list[str] | None = None) -> None:
        self._exclude_patterns = list(exclude_patterns or [])

    async def apply(
        self,
        candidates: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Return candidates whose title does not match any exclusion pattern.

        Args:
            candidates: Parsed entries with title already populated.

        Returns:
            Entries that passed regex title filtering.
        """
        await asyncio.sleep(0)
        if not candidates:
            return []

        patterns = [*_DEFAULT_EXCLUDE_PATTERNS, *self._exclude_patterns]
        if not patterns:
            return candidates

        compiled = [re.compile(p) for p in patterns]
        accepted: list[AnimeRelease] = []

        for candidate in candidates:
            matched = next(
                (r.pattern for r in compiled if r.search(candidate.title)), None
            )
            if matched:
                logger.debug(
                    f"Regex filter: excluding {candidate.title} "
                    f"(matched pattern={matched})"
                )
                continue
            accepted.append(candidate)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.debug(f"Regex filter: {len(accepted)} accepted, {skipped} excluded")
        return accepted
