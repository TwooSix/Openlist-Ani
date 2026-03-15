"""
Resource download priority filtering.

This module decides which RSS entries should actually be downloaded
based on user-configured priority rules (fansub group, language,
video quality) and what has already been downloaded for the same
(anime_name, season, episode).

**Version bypass**: a candidate whose ``version`` is higher than any
previously downloaded version (same anime/season/episode/fansub/languages,
ignoring quality) is always allowed through.

**Quality default**: quality priority defaults to ``2160p > 1080p > 720p > 480p``.
All other fields default to no priority (everything passes).
"""

from __future__ import annotations

from ...config import config
from ...database import db
from ...logger import logger
from ..website.model import AnimeResourceInfo

# Type alias for the episode-level grouping key.
_EpisodeKey = tuple[str, int, int]  # (anime_name, season, episode)


class ResourcePriorityFilter:
    """Filters a batch of parsed resources according to priority rules.

    Designed to be instantiated once per dispatch cycle.  Config values
    are read from the hot-reloadable ``config.priority`` on every call
    to ``filter_batch``, so changes in *config.toml* take effect without
    a restart.
    """

    # ── public API ───────────────────────────────────────────────────

    async def filter_batch(
        self,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Return the subset of *candidates* that should be downloaded.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed priority filtering.
        """
        if not candidates:
            return []

        priority_cfg = config.rss.priority
        fansub_list = priority_cfg.fansub
        lang_list = priority_cfg.languages
        quality_list = priority_cfg.quality
        field_order = priority_cfg.field_order

        # Fast path: no priority rules at all → pass everything through.
        if not fansub_list and not lang_list and not quality_list:
            return candidates

        # Group by (anime_name, season, episode).
        groups = self._group_by_episode(candidates)
        accepted: list[AnimeResourceInfo] = []

        for key, group in groups.items():
            filtered = await self._filter_group(
                key, group, fansub_list, lang_list, quality_list, field_order
            )
            accepted.extend(filtered)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.info(f"Priority filter: {len(accepted)} accepted, {skipped} skipped")
        return accepted

    # ── grouping ─────────────────────────────────────────────────────

    @staticmethod
    def _group_by_episode(
        candidates: list[AnimeResourceInfo],
    ) -> dict[_EpisodeKey | None, list[AnimeResourceInfo]]:
        """Group candidates by (anime_name, season, episode).

        Entries missing any of the three fields are put into a ``None``
        group and will bypass priority filtering entirely (not enough
        metadata to make decisions).
        """
        groups: dict[_EpisodeKey | None, list[AnimeResourceInfo]] = {}
        for c in candidates:
            if c.anime_name is None or c.season is None or c.episode is None:
                groups.setdefault(None, []).append(c)
            else:
                key: _EpisodeKey = (c.anime_name, c.season, c.episode)
                groups.setdefault(key, []).append(c)
        return groups

    # ── per-group filtering ──────────────────────────────────────────

    async def _filter_group(
        self,
        key: _EpisodeKey | None,
        group: list[AnimeResourceInfo],
        fansub_list: list[str],
        lang_list: list[str],
        quality_list: list[str],
        field_order: list[str],
    ) -> list[AnimeResourceInfo]:
        """Filter a single episode group against DB + batch-internal rules."""
        # Groups without a valid key bypass filtering.
        if key is None:
            return group

        anime_name, season, episode = key
        downloaded = await db.find_resources_by_episode(anime_name, season, episode)

        accepted: list[AnimeResourceInfo] = []
        remaining: list[AnimeResourceInfo] = []

        for candidate in group:
            # Version bypass: always allow higher versions through.
            if self._is_version_upgrade(candidate, downloaded):
                logger.debug(f"Priority: version upgrade bypass for {candidate.title}")
                accepted.append(candidate)
                continue

            # DB priority check: skip if a better resource was already downloaded.
            if downloaded and self._should_skip_by_db(
                candidate,
                downloaded,
                fansub_list,
                lang_list,
                quality_list,
                field_order,
            ):
                logger.info(
                    f"Priority: skipping {candidate.title} "
                    f"(higher-priority resource already downloaded)"
                )
                continue

            remaining.append(candidate)

        # Batch-internal selection among the remaining candidates.
        if len(remaining) > 1:
            best = self._select_best_in_batch(
                remaining, fansub_list, lang_list, quality_list, field_order
            )
            accepted.extend(best)
        else:
            accepted.extend(remaining)

        return accepted

    # ── version bypass ───────────────────────────────────────────────

    @staticmethod
    def _is_version_upgrade(
        candidate: AnimeResourceInfo,
        downloaded: list[dict],
    ) -> bool:
        """Return True if *candidate* is a version upgrade over existing records.

        Matching criteria: same fansub + same languages (ignoring quality).
        """
        candidate_langs = "".join(lang.value for lang in candidate.languages)

        for rec in downloaded:
            same_fansub = (rec["fansub"] or "") == (candidate.fansub or "")
            same_langs = (rec["languages"] or "") == candidate_langs
            if same_fansub and same_langs:
                rec_version = rec["version"] or 1
                if candidate.version > rec_version:
                    return True
        return False

    # ── DB priority check (lexicographic) ────────────────────────────

    def _should_skip_by_db(
        self,
        candidate: AnimeResourceInfo,
        downloaded: list[dict],
        fansub_list: list[str],
        lang_list: list[str],
        quality_list: list[str],
        field_order: list[str],
    ) -> bool:
        """Return True if already-downloaded records dominate *candidate*.

        Fields are compared in *field_order* order (lexicographic).
        The first field where the candidate differs from the best
        downloaded level determines the outcome:

        - Candidate strictly better → **allow** (return ``False``).
        - Candidate strictly worse  → **skip**  (return ``True``).
        - Tied → continue to the next field.
        - All fields tied → **allow**.
        """
        for field in field_order:
            cand_level, best_dl = self._field_levels(
                field,
                candidate,
                downloaded,
                fansub_list,
                lang_list,
                quality_list,
            )
            if cand_level is None and best_dl is None:
                continue  # both unranked → tied
            if best_dl is None:
                return False  # nothing ranked downloaded → candidate is better
            if cand_level is None:
                return True  # candidate unranked, downloaded ranked → worse
            if cand_level < best_dl:
                return False  # candidate strictly better
            if cand_level > best_dl:
                return True  # candidate strictly worse
            # equal → continue
        return False

    def _field_levels(
        self,
        field: str,
        candidate: AnimeResourceInfo,
        downloaded: list[dict],
        fansub_list: list[str],
        lang_list: list[str],
        quality_list: list[str],
    ) -> tuple[int | None, int | None]:
        """Return ``(candidate_level, best_downloaded_level)`` for *field*."""
        if field == "fansub" and fansub_list:
            cand = _index_or_none(candidate.fansub or "", fansub_list)
            best = _best_field_level(
                [rec["fansub"] or "" for rec in downloaded],
                fansub_list,
            )
            return cand, best

        if field == "quality" and quality_list:
            cand_val = candidate.quality.value if candidate.quality else ""
            cand = _index_or_none(cand_val, quality_list)
            best = _best_field_level(
                [rec["quality"] or "" for rec in downloaded],
                quality_list,
            )
            return cand, best

        if field == "languages" and lang_list:
            cand = self._get_language_level(candidate, lang_list)
            best = self._get_best_downloaded_language_level(downloaded, lang_list)
            return cand, best

        return None, None  # unknown or empty field → skip

    # ── language helpers ─────────────────────────────────────────────

    @staticmethod
    def _get_language_level(
        candidate: AnimeResourceInfo,
        lang_list: list[str],
    ) -> int | None:
        """Return the best (lowest) priority index among the candidate's languages."""
        best: int | None = None
        for lang in candidate.languages:
            idx = _index_or_none(lang.value, lang_list)
            if idx is not None and (best is None or idx < best):
                best = idx
        return best

    @staticmethod
    def _get_best_downloaded_language_level(
        downloaded: list[dict],
        lang_list: list[str],
    ) -> int | None:
        """Return the best priority index among all downloaded language sets."""
        best: int | None = None
        for rec in downloaded:
            lang_str = rec["languages"] or ""
            for ch in lang_str:
                idx = _index_or_none(ch, lang_list)
                if idx is not None and (best is None or idx < best):
                    best = idx
        return best

    # ── batch-internal lexicographic selection ──────────────────────

    def _select_best_in_batch(
        self,
        candidates: list[AnimeResourceInfo],
        fansub_list: list[str],
        lang_list: list[str],
        quality_list: list[str],
        field_order: list[str],
    ) -> list[AnimeResourceInfo]:
        """Keep only the lexicographically best candidates in a batch.

        Candidates are ranked by the configured *field_order*.  Only
        those tied for the best rank survive.  ``None`` (unranked) is
        treated as worse than any ranked value.
        """
        levels = [
            self._compute_priority_levels(
                c, fansub_list, lang_list, quality_list, field_order
            )
            for c in candidates
        ]

        best = min(levels, key=_level_sort_key)
        result = [c for c, lvl in zip(candidates, levels) if lvl == best]

        if len(result) < len(candidates):
            logger.debug(
                f"Batch filter: kept {len(result)}/{len(candidates)} candidates"
            )
        return result

    def _compute_priority_levels(
        self,
        candidate: AnimeResourceInfo,
        fansub_list: list[str],
        lang_list: list[str],
        quality_list: list[str],
        field_order: list[str],
    ) -> tuple[int | None, ...]:
        """Compute a tuple of priority indices in *field_order* order.

        Lower index = higher priority.  ``None`` means unranked.
        """
        levels: list[int | None] = []
        for field in field_order:
            if field == "fansub" and fansub_list:
                levels.append(_index_or_none(candidate.fansub or "", fansub_list))
            elif field == "quality" and quality_list:
                val = candidate.quality.value if candidate.quality else ""
                levels.append(_index_or_none(val, quality_list))
            elif field == "languages" and lang_list:
                levels.append(self._get_language_level(candidate, lang_list))
        return tuple(levels)


# ── module-level helpers ─────────────────────────────────────────────


def _index_or_none(value: str, lst: list[str]) -> int | None:
    """Return the index of *value* in *lst*, or ``None`` if absent."""
    try:
        return lst.index(value)
    except ValueError:
        return None


def _best_field_level(values: list[str], priority_list: list[str]) -> int | None:
    """Return the best (lowest) priority index among *values*."""
    best: int | None = None
    for v in values:
        idx = _index_or_none(v, priority_list)
        if idx is not None and (best is None or idx < best):
            best = idx
    return best


def _level_sort_key(levels: tuple[int | None, ...]) -> tuple[float, ...]:
    """Convert a priority-level tuple to a sortable key.

    ``None`` (unranked) is mapped to ``inf`` so it sorts last.
    """
    return tuple(x if x is not None else float("inf") for x in levels)
