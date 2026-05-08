from __future__ import annotations

from .aniapi import AniApiFeedSource
from .common import CommonRSSFeedSource
from .feed_source import FeedSource
from .mikan import MikanFeedSource
from .registry import FeedSourceRegistry


class FeedSourceFactory:
    """Create feed source adapters through a replaceable registry."""

    def __init__(self, registry: FeedSourceRegistry | None = None) -> None:
        self._registry = registry or default_feed_source_registry()

    def create(self, url: str) -> FeedSource:
        try:
            return self._registry.create(url)
        except Exception as e:
            raise ValueError(f"Failed to pick feed source for '{url}': {e}") from e


def default_feed_source_registry() -> FeedSourceRegistry:
    registry = FeedSourceRegistry(fallback=CommonRSSFeedSource)
    registry.register_domains(("mikanani.me", "mikanime.tv"), MikanFeedSource)
    registry.register_domains(("ani.rip", "api.ani.rip"), AniApiFeedSource)
    return registry
