import asyncio
from typing import TYPE_CHECKING, List, Optional

from ..config import config
from ..database import db
from ..logger import logger
from .website import AnimeResourceInfo, WebsiteBase, WebsiteFactory

if TYPE_CHECKING:
    from .download.manager import DownloadManager


class RSSManager:
    """Manager for RSS feed subscriptions.

    Handles fetching and parsing RSS feeds from multiple sources,
    checking for duplicates, and filtering already-downloaded content.
    """

    def __init__(self, download_manager: "DownloadManager"):
        """Initialize RSS Manager.

        Args:
            download_manager: DownloadManager for checking active tasks
        """
        self._download_manager = download_manager
        self._factory = WebsiteFactory()

    async def check_update(self) -> List[AnimeResourceInfo]:
        """Check all RSS subscriptions for updates.

        Returns:
            List of new anime resources that are not downloaded
            and not currently being processed.
        """
        urls = config.rss.urls
        if not urls:
            return []

        tasks = self._build_fetch_tasks(urls)
        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        new_entries = await self._collect_new_entries(results)

        if new_entries:
            logger.info(f"RSS check: {len(new_entries)} new entries found")
        else:
            logger.debug("RSS check: no new entries")

        return new_entries

    def _get_website_handler(self, url: str) -> Optional[WebsiteBase]:
        """Get appropriate handler using WebsiteFactory."""
        try:
            return self._factory.create(url)
        except Exception as e:
            logger.warning(f"Failed to create handler for URL {url}: {e}")
            return None

    def _build_fetch_tasks(self, urls: List[str]) -> List:
        """Build RSS fetch coroutine tasks for configured URLs."""
        tasks = []
        for url in urls:
            handler = self._get_website_handler(url)
            if handler is None:
                continue
            tasks.append(handler.fetch_feed(url))
        return tasks

    async def _collect_new_entries(self, results: List) -> List[AnimeResourceInfo]:
        """Collect valid and new entries from fetched RSS results."""
        new_entries: List[AnimeResourceInfo] = []
        for result in results:
            if not self._is_valid_feed_result(result):
                continue

            for entry in result:
                if await self._should_skip_entry(entry):
                    continue
                new_entries.append(entry)
        return new_entries

    def _is_valid_feed_result(self, result) -> bool:
        """Validate a single fetch result and log errors if needed."""
        if isinstance(result, Exception):
            logger.error(f"Error fetching RSS: {result}")
            return False

        if not isinstance(result, list):
            logger.error(f"Unexpected RSS fetch result: {result}")
            return False

        return True

    async def _should_skip_entry(self, entry: AnimeResourceInfo) -> bool:
        """Check whether an entry should be skipped from download queue."""
        if not entry.download_url:
            return True

        if await db.is_downloaded(entry.title):
            logger.debug(f"Skipping already downloaded: {entry.title}")
            return True

        if self._download_manager and self._download_manager.is_downloading(entry):
            logger.debug(f"Skipping already queued: {entry.title}")
            return True

        return False
