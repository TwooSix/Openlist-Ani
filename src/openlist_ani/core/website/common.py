import aiohttp

from ...logger import logger
from .base import WebsiteBase
from .model import AnimeResourceInfo


class CommonRSSWebsite(WebsiteBase):
    """
    Generic website parser for common RSS feeds
    """

    def _get_download_url(self, entry) -> str | None:
        """Extract download link from enclosures or link attribute."""
        for enclosure in entry.get("enclosures", []):
            href = enclosure.get("href", "")
            # Support both magnet links and .torrent files
            if href.startswith("magnet:") or href.endswith(".torrent"):
                return href
            # Some sites use type to indicate torrent
            if enclosure.get("type") == "application/x-bittorrent":
                return href

        # Fallback to link attribute
        link = getattr(entry, "link", "")
        if link and (link.startswith("magnet:") or link.endswith(".torrent")):
            return link

        return None

    async def parse_entry(
        self, entry, session: aiohttp.ClientSession
    ) -> AnimeResourceInfo | None:
        title = getattr(entry, "title", None)
        download_url = self._get_download_url(entry)

        if not download_url or not title:
            logger.debug("Skipping entry without title or download URL")
            return None

        return AnimeResourceInfo(
            title=title,
            download_url=download_url,
            anime_name=None,
            season=None,
            fansub=None,
        )
