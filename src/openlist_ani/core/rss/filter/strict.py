"""
Strict rename-based duplicate filter.

When enabled via ``[rss] strict = true``, this filter computes the rename
stem for each candidate entry and compares it against stems derived from
already-downloaded records in the database.  Candidates whose stem matches
an existing download are filtered out **unless** the candidate is a
version upgrade.
"""

from __future__ import annotations

from ....config import config
from ....database import db
from ....logger import logger
from ...website.model import AnimeResourceInfo
from .base import EpisodeKey, group_by_episode


def compute_rename_stem(
    rename_format: str,
    anime_name: str,
    season: int,
    episode: int,
    fansub: str | None = None,
    quality: str | None = None,
    languages: str = "",
) -> str:
    """Compute the rename stem from metadata and a format string.

    Args:
        rename_format: Python format string with named placeholders.
        anime_name: Anime series name.
        season: Season number.
        episode: Episode number.
        fansub: Fansub group name (or ``None``).
        quality: Video quality string, e.g. ``"1080p"`` (or ``None``).
        languages: Joined language string, e.g. ``"简繁"``.

    Returns:
        The formatted stem string, stripped of leading/trailing whitespace.
        Falls back to ``"{anime_name} S{season:02d}E{episode:02d}"`` on
        format errors.
    """
    context: dict[str, str | int] = {
        "anime_name": anime_name or "",
        "season": season or 0,
        "episode": episode or 0,
        "fansub": fansub or "",
        "quality": quality or "",
        "languages": languages or "",
    }
    try:
        return rename_format.format(**context).strip()
    except (KeyError, ValueError, IndexError):
        name = anime_name or ""
        s = season or 0
        e = episode or 0
        return f"{name} S{s:02d}E{e:02d}"


def _stem_from_resource(rename_format: str, resource: AnimeResourceInfo) -> str:
    """Build a rename stem from an ``AnimeResourceInfo`` instance."""
    quality_str = str(resource.quality) if resource.quality else ""
    languages_str = "".join(str(lang) for lang in resource.languages)
    return compute_rename_stem(
        rename_format,
        anime_name=resource.anime_name or "",
        season=resource.season or 0,
        episode=resource.episode or 0,
        fansub=resource.fansub,
        quality=quality_str,
        languages=languages_str,
    )


def _stem_from_db_record(
    rename_format: str,
    anime_name: str,
    season: int,
    episode: int,
    record: dict,
) -> str:
    """Build a rename stem from a database record dict."""
    return compute_rename_stem(
        rename_format,
        anime_name=anime_name,
        season=season,
        episode=episode,
        fansub=record.get("fansub"),
        quality=record.get("quality"),
        languages=record.get("languages", ""),
    )


class StrictRenameFilter:
    """Filter candidates whose rename stem duplicates an existing download.

    This filter reads ``config.openlist.rename_format`` on every call
    (hot-reloadable) and compares each candidate's computed rename stem
    against stems built from DB records of the same episode.

    A candidate is **allowed through** if:

    - Its stem does not match any existing download, OR
    - It is a version upgrade (higher ``version`` than the matched record).

    Within the same batch, if multiple candidates produce the same stem
    only the one with the highest version is kept.
    """

    async def apply(
        self,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Filter *candidates* by rename-stem deduplication.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed strict rename filtering.
        """
        if not candidates:
            return []

        rename_format = config.openlist.rename_format

        groups = group_by_episode(candidates)
        accepted: list[AnimeResourceInfo] = []

        for key, group in groups.items():
            if key is None:
                # Not enough metadata to compute a stem — bypass.
                accepted.extend(group)
                continue
            filtered = await self._filter_group(rename_format, key, group)
            accepted.extend(filtered)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.info(f"Strict filter: {len(accepted)} accepted, {skipped} skipped")
        return accepted

    # ── per-group filtering ──────────────────────────────────────────

    async def _filter_group(
        self,
        rename_format: str,
        key: EpisodeKey,
        group: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Filter a single episode group against DB stems + intra-batch dedup."""
        anime_name, season, episode = key

        db_records = await db.find_resources_by_episode(anime_name, season, episode)
        db_stems: list[tuple[str, int]] = [
            (
                _stem_from_db_record(rename_format, anime_name, season, episode, rec),
                rec.get("version") or 1,
            )
            for rec in db_records
        ]

        # Phase 1: filter against DB records.
        after_db: list[AnimeResourceInfo] = []
        for candidate in group:
            cand_stem = _stem_from_resource(rename_format, candidate)

            if self._is_blocked_by_db(cand_stem, candidate.version, db_stems):
                logger.info(
                    f"Strict: skipping {candidate.title} "
                    f"(rename stem matches existing download)"
                )
                continue
            after_db.append(candidate)

        # Phase 2: intra-batch dedup — same stem keeps highest version only.
        return self._dedup_batch(rename_format, after_db)

    # ── DB comparison ────────────────────────────────────────────────

    @staticmethod
    def _is_blocked_by_db(
        candidate_stem: str,
        candidate_version: int,
        db_stems: list[tuple[str, int]],
    ) -> bool:
        """Return True if *candidate_stem* duplicates a DB entry.

        The candidate is allowed through (returns ``False``) if its
        version is strictly higher than the matching DB record.
        """
        for stem, version in db_stems:
            if candidate_stem == stem:
                if candidate_version > version:
                    return False  # version upgrade → allow
                return True  # same or lower version → block
        return False

    # ── intra-batch dedup ────────────────────────────────────────────

    @staticmethod
    def _dedup_batch(
        rename_format: str,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Within a batch, keep only the highest-version entry per stem."""
        stem_best: dict[str, AnimeResourceInfo] = {}
        for c in candidates:
            stem = _stem_from_resource(rename_format, c)
            existing = stem_best.get(stem)
            if existing is None or c.version > existing.version:
                stem_best[stem] = c
        return list(stem_best.values())
