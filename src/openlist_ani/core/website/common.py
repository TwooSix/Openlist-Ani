from urllib.parse import urlparse

import aiohttp

from ...logger import logger
from .base import WebsiteBase
from .model import AnimeResourceInfo


def _is_torrent_url(url: str) -> bool:
    """Check if URL points to a .torrent file (ignoring query params).

    Args:
        url: URL string to check

    Returns:
        True if the URL path ends with '.torrent'
    """
    try:
        return urlparse(url).path.endswith(".torrent")
    except Exception:
        return url.endswith(".torrent")


class CommonRSSWebsite(WebsiteBase):
    """
    Generic website parser for common RSS feeds
    """

    def _get_download_url(self, entry) -> str | None:
        """Extract download link from enclosures or link attribute."""
        for enclosure in entry.get("enclosures", []):
            href = enclosure.get("href", "")
            # Support both magnet links and .torrent files
            if href.startswith("magnet:") or _is_torrent_url(href):
                return href
            # Some sites use type to indicate torrent
            if enclosure.get("type") == "application/x-bittorrent":
                return href

        link = getattr(entry, "link", "")
        if link and (link.startswith("magnet:") or _is_torrent_url(link)):
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
