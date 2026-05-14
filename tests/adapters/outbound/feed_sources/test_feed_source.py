from __future__ import annotations

import asyncio

from openlist_ani.adapters.outbound.feed_sources.feed_source import FeedSource
from openlist_ani.domain.anime_release import AnimeRelease


class CountingFeedSource(FeedSource):
    entry_concurrency = 2

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def parse_entry(self, entry, session) -> AnimeRelease | None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return AnimeRelease(title=f"Anime {entry}", download_url=f"magnet:?{entry}")


async def test_parse_entries_respects_source_concurrency_limit():
    source = CountingFeedSource()

    entries = await source._parse_entries(list(range(6)), session=object())

    assert len(entries) == 6
    assert source.max_active <= 2
