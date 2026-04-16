"""
Metadata-based blacklist filter.

Filters out RSS entries whose LLM-parsed metadata (fansub, quality,
languages) matches any value in the user-configured exclusion lists
under ``[rss.filter]``.
"""

from __future__ import annotations

from ....config import MetadataFilterConfig, config
from ....logger import logger
from ...website.model import AnimeResourceInfo


class MetadataFilter:
    """Filter candidates by metadata blacklists.

    Config values are read from the hot-reloadable
    ``config.rss.filter`` on every call to ``apply``,
    so changes in *config.toml* take effect without a restart.
    """

    def apply(
        self,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Return candidates not matching any metadata exclusion rule.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed metadata filtering.
        """
        if not candidates:
            return []

        filter_cfg = config.rss.filter
        if (
            not filter_cfg.exclude_fansub
            and not filter_cfg.exclude_quality
            and not filter_cfg.exclude_languages
        ):
            return candidates

        accepted: list[AnimeResourceInfo] = []
        for candidate in candidates:
            if self._is_excluded(candidate, filter_cfg):
                logger.info(
                    f"Metadata filter: excluding {candidate.title} "
                    f"(matched metadata blacklist)"
                )
                continue
            accepted.append(candidate)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.info(
                f"Metadata filter: {len(accepted)} accepted, {skipped} excluded"
            )
        return accepted

    @staticmethod
    def _is_excluded(
        candidate: AnimeResourceInfo,
        filter_cfg: MetadataFilterConfig,
    ) -> bool:
        """Return True if *candidate* matches any exclusion rule."""
        # Fansub: exact match
        if (
            filter_cfg.exclude_fansub
            and candidate.fansub in filter_cfg.exclude_fansub
        ):
            return True

        # Quality: match against .value string
        if (
            filter_cfg.exclude_quality
            and candidate.quality
            and candidate.quality.value in filter_cfg.exclude_quality
        ):
            return True

        # Languages: any language in exclusion list
        if filter_cfg.exclude_languages:
            for lang in candidate.languages:
                if lang.value in filter_cfg.exclude_languages:
                    return True

        return False
