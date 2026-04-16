"""Tests for FilterChain orchestrator."""

from __future__ import annotations

from openlist_ani.core.rss.filter.base import FilterChain
from openlist_ani.core.website.model import AnimeResourceInfo


def _make_resource(title: str = "test") -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=urn:btih:abc")


class _PassAllFilter:
    """Filter that passes everything through."""

    def apply(
        self, candidates: list[AnimeResourceInfo]
    ) -> list[AnimeResourceInfo]:
        return candidates


class _DropAllFilter:
    """Filter that drops everything."""

    def apply(
        self, candidates: list[AnimeResourceInfo]
    ) -> list[AnimeResourceInfo]:
        return []


class _KeepFirstFilter:
    """Filter that keeps only the first candidate."""

    def apply(
        self, candidates: list[AnimeResourceInfo]
    ) -> list[AnimeResourceInfo]:
        return candidates[:1] if candidates else []


class TestFilterChain:
    async def test_empty_chain_passes_all(self):
        """An empty chain should pass all candidates through."""
        chain = FilterChain()
        entries = [_make_resource("a"), _make_resource("b")]
        result = await chain.apply(entries)
        assert len(result) == 2

    async def test_single_filter_applied(self):
        """A chain with one filter should apply that filter."""
        chain = FilterChain([_KeepFirstFilter()])
        entries = [_make_resource("a"), _make_resource("b")]
        result = await chain.apply(entries)
        assert len(result) == 1
        assert result[0].title == "a"

    async def test_multiple_filters_in_order(self):
        """Filters should execute in insertion order."""
        chain = FilterChain()
        chain.add_filter(_PassAllFilter())
        chain.add_filter(_KeepFirstFilter())
        entries = [_make_resource("a"), _make_resource("b"), _make_resource("c")]
        result = await chain.apply(entries)
        assert len(result) == 1
        assert result[0].title == "a"

    async def test_short_circuit_on_empty(self):
        """If a filter returns empty, subsequent filters are skipped."""
        call_count = 0

        class _CountingFilter:
            def apply(
                self, candidates: list[AnimeResourceInfo]
            ) -> list[AnimeResourceInfo]:
                nonlocal call_count
                call_count += 1
                return candidates

        chain = FilterChain()
        chain.add_filter(_DropAllFilter())
        chain.add_filter(_CountingFilter())

        entries = [_make_resource("a")]
        result = await chain.apply(entries)
        assert result == []
        assert call_count == 0  # second filter never called

    async def test_empty_input(self):
        """Empty input should pass through without issues."""
        chain = FilterChain([_PassAllFilter()])
        result = await chain.apply([])
        assert result == []
