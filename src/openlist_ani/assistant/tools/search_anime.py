"""
Search anime resources tool.
"""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from ...core.website import WebsiteFactory
from ...database import db
from ...logger import logger
from ..model import SearchResult
from .base import BaseTool


class SearchAnimeTool(BaseTool):
    """Tool for searching anime resources on websites."""

    @property
    def name(self) -> str:
        return "search_anime_resources"

    @property
    def description(self) -> str:
        return "Search for anime resources on specified website. Returns list of resources with download URLs and metadata."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "anime_name": {
                    "type": "string",
                    "description": "The anime name to search for",
                },
                "website": {
                    "type": "string",
                    "description": "Website to search on. Options: mikan (蜜柑计划), dmhy (动漫花园), acgrip (ACG.RIP)",
                    "enum": ["mikan", "dmhy", "acgrip"],
                },
            },
            "required": ["anime_name", "website"],
        }

    async def execute(self, anime_name: str, website: str, **kwargs) -> str:
        """Search anime resources on specified website.

        Args:
            anime_name: Anime name to search
            website: Website to search on

        Returns:
            Formatted search results
        """
        logger.info(f"Assistant: Searching {anime_name} on {website}")

        search_url = self._build_search_url(anime_name, website)
        if not search_url:
            logger.error(f"Assistant: Unsupported website {website}")
            return f"❌ Unsupported website: {website}"

        try:
            entries = await self._fetch_entries(search_url)
            if entries is None:
                logger.error(f"Assistant: Failed to create handler for {search_url}")
                return f"❌ Failed to create handler for {website}"

            logger.info(f"Assistant: Found {len(entries)} results for {anime_name}")
            results = await self._build_search_results(entries)
        except Exception:
            logger.exception(f"Assistant: Error searching {anime_name} on {website}")
            return f"❌ Error searching {anime_name} on {website}"

        if not results:
            return f"❌ No resources found for {anime_name} on {website}"

        return self._format_search_results(anime_name, website, results)

    def _build_search_url(self, anime_name: str, website: str) -> Optional[str]:
        """Build search RSS URL based on website type."""
        encoded_name = quote_plus(anime_name)
        website_search_urls = {
            "mikan": f"https://mikanani.me/RSS/Search?searchstr={encoded_name}",
            "dmhy": f"https://dmhy.org/topics/rss/rss.xml?keyword={encoded_name}",
            "acgrip": f"https://acg.rip/.xml?term={encoded_name}",
        }
        return website_search_urls.get(website)

    async def _fetch_entries(self, search_url: str) -> Optional[List[Any]]:
        """Fetch RSS entries through website handler."""
        factory = WebsiteFactory()
        handler = factory.create(search_url)
        if not handler:
            return None
        return await handler.fetch_feed(search_url)

    async def _build_search_results(self, entries: List[Any]) -> List[SearchResult]:
        """Convert raw feed entries to SearchResult list with download status."""
        results: List[SearchResult] = []
        for entry in entries:
            is_downloaded = await db.is_downloaded(entry.title)
            results.append(
                SearchResult(
                    title=entry.title,
                    download_url=entry.download_url or "",
                    is_downloaded=is_downloaded,
                    anime_name=entry.anime_name,
                    episode=entry.episode,
                    quality=entry.quality.value if entry.quality else None,
                )
            )
        return results

    def _split_results(
        self, results: List[SearchResult]
    ) -> Tuple[List[SearchResult], List[SearchResult]]:
        """Split results into downloaded and new resources."""
        downloaded = [result for result in results if result.is_downloaded]
        new_resources = [result for result in results if not result.is_downloaded]
        return downloaded, new_resources

    def _format_search_results(
        self, anime_name: str, website: str, results: List[SearchResult]
    ) -> str:
        """Format search results for assistant response."""
        downloaded, new_resources = self._split_results(results)

        msg = f"🔍 Search Results for '{anime_name}' on {website}:\n\n"

        if downloaded:
            msg += self._format_downloaded_resources(downloaded)

        if new_resources:
            msg += self._format_new_resources(new_resources)
        else:
            msg += "ℹ️ No new resources found (all have been downloaded)\n"

        return msg

    def _format_downloaded_resources(self, downloaded: List[SearchResult]) -> str:
        """Format downloaded resources section."""
        message = f"📦 Already Downloaded ({len(downloaded)} resources):\n"
        for idx, resource in enumerate(downloaded[:10], 1):
            message += f"  {idx}. {resource.title}\n"
            if resource.quality:
                message += f"     Quality: {resource.quality}\n"

        if len(downloaded) > 10:
            message += f"  ...and {len(downloaded) - 10} more\n"

        message += (
            "\n⚠️ These resources are already downloaded, "
            "do NOT download them again!\n\n"
        )
        return message

    def _format_new_resources(self, new_resources: List[SearchResult]) -> str:
        """Format new resources section."""
        message = f"🆕 New Resources ({len(new_resources)} available):\n"
        for idx, resource in enumerate(new_resources[:10], 1):
            message += f"  {idx}. {resource.title}\n"
            if resource.quality:
                message += f"     Quality: {resource.quality}\n"
            message += f"     Download URL: {resource.download_url}\n\n"

        if len(new_resources) > 10:
            message += f"  ...and {len(new_resources) - 10} more\n"

        return message
