"""
Regex-based title exclusion filter.

Filters out RSS entries whose title matches any of the user-configured
regular expression patterns in ``config.rss.filter.exclude_patterns``,
plus a built-in blacklist for collection-style titles.
"""

from __future__ import annotations

import re

from ....config import config
from ....logger import logger
from ...website.model import AnimeResourceInfo

# 现阶段不支持合集下载，默认过滤
_DEFAULT_EXCLUDE_PATTERNS = [r"(\d{2}-\d{2}|合集)"]


class RegexTitleFilter:
    """Filter candidates by matching their title against exclusion patterns.

    Config values are read from the hot-reloadable
    ``config.rss.filter.exclude_patterns`` on every call to ``apply``,
    so changes in *config.toml* take effect without a restart.
    A built-in pattern also excludes titles that look like collections.
    """

    def apply(
        self,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Return candidates whose title does not match any exclusion pattern.

        Args:
            candidates: Parsed entries with title already populated.

        Returns:
            Entries that passed regex title filtering.
        """
        if not candidates:
            return []

        configured_patterns = list(config.rss.filter.exclude_patterns or [])
        patterns = [*_DEFAULT_EXCLUDE_PATTERNS, *configured_patterns]
        if not patterns:
            return candidates

        compiled = [re.compile(p) for p in patterns]
        accepted: list[AnimeResourceInfo] = []

        for candidate in candidates:
            if any(r.search(candidate.title) for r in compiled):
                logger.info(
                    f"Regex filter: excluding {candidate.title} "
                    f"(matched exclusion pattern)"
                )
                continue
            accepted.append(candidate)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.info(f"Regex filter: {len(accepted)} accepted, {skipped} excluded")
        return accepted
