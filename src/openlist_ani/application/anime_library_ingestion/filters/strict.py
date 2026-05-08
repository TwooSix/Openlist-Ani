"""
Strict rename-based duplicate filter.

When enabled via ``[rss] strict = true``, this filter computes the rename
stem for each candidate entry and compares it against stems derived from
already-downloaded records in the database.  Candidates whose stem matches
an existing download are filtered out **unless** the candidate is a
version upgrade.
"""

from __future__ import annotations

from openlist_ani.application.anime_library_ingestion.ports import (
    ActiveTaskQueryPort,
    AnimeLibraryRepositoryPort,
)
from openlist_ani.domain.anime_release import (
    AnimeRelease,
    ReleaseDirectoryPlanner,
    ReleaseFilenamePlanner,
    format_release_stem,
)
from openlist_ani.logger import logger

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
    return format_release_stem(
        rename_format,
        anime_name=anime_name,
        season=season,
        episode=episode,
        fansub=fansub,
        quality=quality,
        languages=languages,
        include_version=False,
    )


def _stem_from_release(rename_format: str, release: AnimeRelease) -> str:
    """Build a rename stem from an ``AnimeRelease`` instance."""
    return ReleaseFilenamePlanner(rename_format).stem(release, include_version=False)


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

    A candidate is **allowed through** if:

    - Its stem does not match any existing download, OR
    - It is a version upgrade (higher ``version`` than the matched record).

    Within the same batch, if multiple candidates produce the same stem
    only the one with the highest version is kept.
    """

    def __init__(
        self,
        rename_format: str,
        anime_library_repository: AnimeLibraryRepositoryPort,
        active_task_query: ActiveTaskQueryPort | None = None,
        base_path: str = "",
        directory_planner: ReleaseDirectoryPlanner | None = None,
    ) -> None:
        self._rename_format = rename_format
        self._anime_library_repository = anime_library_repository
        self._active_task_query = active_task_query
        self._base_path = base_path
        self._directory_planner = directory_planner or ReleaseDirectoryPlanner()

    async def apply(
        self,
        candidates: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Filter *candidates* by rename-stem deduplication.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed strict rename filtering.
        """
        if not candidates:
            return []

        groups = group_by_episode(candidates)
        accepted: list[AnimeRelease] = []

        for key, group in groups.items():
            if key is None:
                # Not enough metadata to compute a stem — bypass.
                accepted.extend(group)
                continue
            filtered = await self._filter_group(self._rename_format, key, group)
            accepted.extend(filtered)

        skipped = len(candidates) - len(accepted)
        if skipped:
            logger.debug(f"Strict filter: {len(accepted)} accepted, {skipped} skipped")
        return accepted

    # ── per-group filtering ──────────────────────────────────────────

    async def _filter_group(
        self,
        rename_format: str,
        key: EpisodeKey,
        group: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Filter a single episode group against DB stems + intra-batch dedup."""
        anime_name, season, episode = key

        db_records = await self._anime_library_repository.find_releases_by_episode(
            anime_name, season, episode
        )
        db_stems: list[tuple[str, int]] = [
            (
                _stem_from_db_record(rename_format, anime_name, season, episode, rec),
                rec.get("version") or 1,
            )
            for rec in db_records
        ]
        active_stems = self._active_stems_for_episode(key)

        # Phase 1: filter against DB records and active downloads.
        after_db: list[AnimeRelease] = []
        for candidate in group:
            cand_stem = _stem_from_release(rename_format, candidate)

            if self._is_blocked_by_db(cand_stem, candidate.version, db_stems):
                logger.debug(
                    f"Strict: skipping {candidate.title} "
                    f"(rename stem matches existing download)"
                )
                continue
            if self._is_blocked_by_active(candidate, cand_stem, active_stems):
                logger.debug(
                    f"Strict: skipping {candidate.title} "
                    f"(rename stem matches active download)"
                )
                continue
            after_db.append(candidate)

        # Phase 2: intra-batch dedup — same stem keeps highest version only.
        return self._dedup_batch(rename_format, after_db)

    def _active_stems_for_episode(
        self,
        key: EpisodeKey,
    ) -> list[tuple[str, str, int]]:
        if self._active_task_query is None:
            return []

        anime_name, season, episode = key
        stems: list[tuple[str, str, int]] = []
        for task in self._active_task_query.list_active_tasks():
            release = task.release
            if (
                release.anime_name != anime_name
                or release.season != season
                or release.episode != episode
            ):
                continue
            stems.append(
                (
                    self._directory_planner.target_directory_path(
                        task.base_path,
                        release,
                    ),
                    _stem_from_release(self._rename_format, release),
                    release.version,
                )
            )
        return stems

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

    def _is_blocked_by_active(
        self,
        candidate: AnimeRelease,
        candidate_stem: str,
        active_stems: list[tuple[str, str, int]],
    ) -> bool:
        candidate_directory = self._directory_planner.target_directory_path(
            self._base_path,
            candidate,
        )
        for directory, stem, version in active_stems:
            if (
                candidate_directory == directory
                and candidate_stem == stem
                and candidate.version <= version
            ):
                return True
        return False

    # ── intra-batch dedup ────────────────────────────────────────────

    @staticmethod
    def _dedup_batch(
        rename_format: str,
        candidates: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Within a batch, keep only the highest-version entry per stem."""
        stem_best: dict[str, AnimeRelease] = {}
        for c in candidates:
            stem = _stem_from_release(rename_format, c)
            existing = stem_best.get(stem)
            if existing is None or c.version > existing.version:
                stem_best[stem] = c
        return list(stem_best.values())
