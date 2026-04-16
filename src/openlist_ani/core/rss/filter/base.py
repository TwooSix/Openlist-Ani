"""
Filter chain infrastructure for RSS resource filtering.

Defines the ``ResourceFilter`` protocol and the ``FilterChain`` orchestrator
that runs a sequence of filters in order.  Also provides shared helpers used
by multiple filter implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...website.model import AnimeResourceInfo

# Type alias for the episode-level grouping key.
EpisodeKey = tuple[str, int, int]  # (anime_name, season, episode)


@runtime_checkable
class ResourceFilter(Protocol):
    """Protocol for RSS resource filters.

    Each filter receives a list of candidates and returns the subset
    that should proceed to the next stage.
    """

    async def apply(
        self,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Filter *candidates* and return those that pass.

        Args:
            candidates: Parsed entries with metadata already populated.

        Returns:
            Entries that passed this filter.
        """
        ...


class FilterChain:
    """Execute a sequence of ``ResourceFilter`` instances in order.

    Filters run in insertion order.  If any filter reduces the list to
    empty, remaining filters are skipped (short-circuit).
    """

    def __init__(
        self, filters: list[ResourceFilter] | None = None
    ) -> None:
        self._filters: list[ResourceFilter] = list(filters or [])

    def add_filter(self, resource_filter: ResourceFilter) -> None:
        """Append a filter to the end of the chain."""
        self._filters.append(resource_filter)

    async def apply(
        self,
        candidates: list[AnimeResourceInfo],
    ) -> list[AnimeResourceInfo]:
        """Run all filters in order, piping output to the next.

        Args:
            candidates: Initial list of entries.

        Returns:
            Entries that survived every filter in the chain.
        """
        result = candidates
        for resource_filter in self._filters:
            # Handle both sync and async filters
            if hasattr(resource_filter.apply, '__call__'):
                import inspect
                if inspect.iscoroutinefunction(resource_filter.apply):
                    result = await resource_filter.apply(result)
                else:
                    result = resource_filter.apply(result)
            else:
                result = await resource_filter.apply(result)
            if not result:
                break
        return result


def group_by_episode(
    candidates: list[AnimeResourceInfo],
) -> dict[EpisodeKey | None, list[AnimeResourceInfo]]:
    """Group candidates by ``(anime_name, season, episode)``.

    Entries missing any of the three fields go into the ``None`` group
    and are typically left unfiltered (not enough metadata for decisions).
    """
    groups: dict[EpisodeKey | None, list[AnimeResourceInfo]] = {}
    for c in candidates:
        if c.anime_name is None or c.season is None or c.episode is None:
            groups.setdefault(None, []).append(c)
        else:
            key: EpisodeKey = (c.anime_name, c.season, c.episode)
            groups.setdefault(key, []).append(c)
    return groups
