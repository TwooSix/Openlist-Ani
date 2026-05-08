import asyncio

from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.logger import logger

from .feed_source import FeedSource
from .factory import FeedSourceFactory


class ReleaseFeedReader:
    """Reads releases from configured feed URLs.

    This adapter owns external feed parsing. Application-level duplicate
    filtering is handled by the anime library ingestion use case.
    """

    def __init__(
        self,
        urls: list[str],
        factory: FeedSourceFactory | None = None,
    ) -> None:
        self._urls = urls
        self._factory = factory or FeedSourceFactory()

    async def fetch_new_releases(self) -> list[AnimeRelease]:
        """Fetch releases from all configured feeds."""
        if not self._urls:
            return []

        fetches = self._build_fetch_tasks(self._urls)
        if not fetches:
            return []

        results = await asyncio.gather(
            *(task for _, task in fetches), return_exceptions=True
        )
        return self._collect_entries(list(zip((url for url, _ in fetches), results)))

    def _get_feed_source(self, url: str) -> FeedSource | None:
        """Get appropriate handler using FeedSourceFactory."""
        try:
            return self._factory.create(url)
        except Exception as e:
            logger.warning(f"Failed to create handler for URL {url}: {e}")
            return None

    def _build_fetch_tasks(self, urls: list[str]) -> list[tuple[str, object]]:
        """Build RSS fetch coroutine tasks for configured URLs."""
        tasks = []
        for url in urls:
            handler = self._get_feed_source(url)
            if handler is None:
                continue
            tasks.append((url, handler.fetch_feed(url)))
        return tasks

    def _collect_entries(self, results: list[tuple[str, object]]) -> list[AnimeRelease]:
        """Collect valid entries from fetched RSS results."""
        new_entries: list[AnimeRelease] = []
        for url, result in results:
            if not self._is_valid_feed_result(url, result):
                continue

            for entry in result:
                if not entry.download_url:
                    continue
                new_entries.append(entry)
        return new_entries

    def _is_valid_feed_result(self, url: str, result: object) -> bool:
        """Validate a single fetch result and log errors if needed."""
        if isinstance(result, Exception):
            logger.warning(
                f"RSS source failed for {url}; continuing with other sources: {result}"
            )
            return False

        if not isinstance(result, list):
            logger.warning(f"Unexpected RSS fetch result for {url}: {result}")
            return False

        return True
