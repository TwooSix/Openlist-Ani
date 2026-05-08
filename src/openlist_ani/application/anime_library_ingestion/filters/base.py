"""
Filter chain for RSS release filtering.

Defines the ``ReleaseFilter`` protocol and the ``FilterChain`` orchestrator
that runs a sequence of filters in order.  Also provides shared helpers used
by multiple filter implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from openlist_ani.domain.anime_release import AnimeRelease

# Type alias for the episode-level grouping key.
EpisodeKey = tuple[str, int, int]  # (anime_name, season, episode)


@dataclass(frozen=True)
class FilterReport:
    name: str
    input_count: int
    output_count: int
    skipped_titles: tuple[str, ...] = ()

    @property
    def skipped_count(self) -> int:
        return self.input_count - self.output_count


@runtime_checkable
class ReleaseFilter(Protocol):
    """Protocol for RSS release filters.

    Each filter receives a list of candidates and returns the subset
    that should proceed to the next stage.
    """

    async def apply(
        self,
        candidates: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Filter *candidates* and return those that pass.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed this filter.
        """
        ...


class FilterChain:
    """Execute a sequence of ``ReleaseFilter`` instances in order.

    Filters run in insertion order.  If any filter reduces the list to
    empty, remaining filters are skipped (short-circuit).
    """

    def __init__(self, filters: list[ReleaseFilter] | None = None) -> None:
        self._filters: list[ReleaseFilter] = list(filters or [])
        self._last_report: list[FilterReport] = []

    def add_filter(self, release_filter: ReleaseFilter) -> None:
        """Append a filter to the end of the chain."""
        self._filters.append(release_filter)

    async def apply(
        self,
        candidates: list[AnimeRelease],
    ) -> list[AnimeRelease]:
        """Run all filters in order, piping output to the next.

        Args:
            candidates: Initial list of entries.

        Returns:
            Entries that survived every filter in the chain.
        """
        result = candidates
        self._last_report = []
        for release_filter in self._filters:
            before = result
            input_count = len(before)
            result = await release_filter.apply(before)
            accepted_ids = {id(entry) for entry in result}
            skipped_titles = tuple(
                entry.title for entry in before if id(entry) not in accepted_ids
            )
            self._last_report.append(
                FilterReport(
                    name=_filter_log_name(release_filter),
                    input_count=input_count,
                    output_count=len(result),
                    skipped_titles=skipped_titles,
                )
            )
            if not result:
                break
        return result

    def report_summary(self, include_details: bool = False) -> str:
        parts = [
            _format_report_summary(report, include_details)
            for report in self._last_report
            if report.skipped_count > 0
        ]
        return ", ".join(parts)


def _filter_log_name(release_filter: ReleaseFilter) -> str:
    name = type(release_filter).__name__
    if name.endswith("TitleFilter"):
        name = name[: -len("TitleFilter")]
    elif name.endswith("Filter"):
        name = name[: -len("Filter")]
    return name.lower().removesuffix("rename")


def _format_report_summary(report: FilterReport, include_details: bool) -> str:
    summary = f"{report.name}={report.skipped_count}"
    if not include_details:
        return summary

    sample_count = 3
    samples = list(report.skipped_titles[:sample_count])
    if not samples:
        return summary

    remainder = report.skipped_count - len(samples)
    sample_text = "; ".join(samples)
    if remainder > 0:
        sample_text = f"{sample_text}; +{remainder} more"
    return f"{summary} ({_filter_reason(report.name)}: {sample_text})"


def _filter_reason(name: str) -> str:
    reasons = {
        "regex": "title pattern matched",
        "metadata": "metadata rule matched",
        "priority": "lower priority than another release",
        "strict": "duplicate target filename or already downloaded",
    }
    return reasons.get(name, "filtered out")


def group_by_episode(
    candidates: list[AnimeRelease],
) -> dict[EpisodeKey | None, list[AnimeRelease]]:
    """Group candidates by ``(anime_name, season, episode)``.

    Entries missing any of the three fields go into the ``None`` group
    and are typically left unfiltered (not enough metadata for decisions).
    """
    groups: dict[EpisodeKey | None, list[AnimeRelease]] = {}
    for c in candidates:
        if c.anime_name is None or c.season is None or c.episode is None:
            groups.setdefault(None, []).append(c)
        else:
            key: EpisodeKey = (c.anime_name, c.season, c.episode)
            groups.setdefault(key, []).append(c)
    return groups
