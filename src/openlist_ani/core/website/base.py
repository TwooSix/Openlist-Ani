import asyncio
from abc import ABC, abstractmethod

import aiohttp
import feedparser

from ...logger import logger
from .model import AnimeResourceInfo


class WebsiteBase(ABC):
    """
    Abstract base class for website RSS parsers.
    """

    async def fetch_feed(self, url: str) -> list[AnimeResourceInfo]:
        """Fetch and parse RSS feed from a URL.

        Args:
            url: RSS feed URL

        Returns:
            List of parsed anime resource entries
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

                    entries: list[AnimeResourceInfo] = []
                    for res in results:
                        if isinstance(res, Exception):
                            logger.warning(
                                f"Failed to parse RSS entry: {res}"
                            )
                            continue
                        if res:
                            entries.append(res)

                    return entries
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"RSS fetch failed for {url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching RSS {url}: {e}")
            return []

    @abstractmethod
    async def parse_entry(
        self, entry, session: aiohttp.ClientSession
    ) -> AnimeResourceInfo | None:
        """Parse a single RSS entry.

        Args:
            entry: feedparser entry object
            session: Active aiohttp session for fetching additional data

        Returns:
            Parsed AnimeResourceInfo or None if parsing fails
        """
        pass
