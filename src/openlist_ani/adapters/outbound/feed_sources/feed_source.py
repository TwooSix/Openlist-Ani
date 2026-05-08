import asyncio
from abc import ABC, abstractmethod

import aiohttp
import feedparser

from openlist_ani.logger import logger
from openlist_ani.domain.anime_release import AnimeRelease


class FeedSource(ABC):
    """
    Abstract base class for RSS feed sources.
    """

    async def fetch_feed(self, url: str) -> list[AnimeRelease]:
        """Fetch and parse RSS feed from a URL.

        Args:
            url: RSS feed URL

        Returns:
            List of parsed anime releases
        """
        timeout = aiohttp.ClientTimeout(total=30)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=True
            ) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    content = await response.text()

                    feed = feedparser.parse(content)

                    tasks = [self.parse_entry(entry, session) for entry in feed.entries]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    entries: list[AnimeRelease] = []
                    for res in results:
                        if isinstance(res, Exception):
                            logger.warning(f"Failed to parse RSS entry: {res}")
                            continue
                        if res:
                            entries.append(res)

                    return entries
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"RSS fetch failed for {url}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Unexpected RSS fetch failure for {url}: {e}")
            return []

    @abstractmethod
    async def parse_entry(
        self, entry, session: aiohttp.ClientSession
    ) -> AnimeRelease | None:
        """Parse a single RSS entry.

        Args:
            entry: feedparser entry object
            session: Active aiohttp session for fetching additional data

        Returns:
            Parsed AnimeRelease or None if parsing fails
        """
        pass
