from urllib.parse import urlparse

import aiohttp

from openlist_ani.logger import logger
from .feed_source import FeedSource
from openlist_ani.domain.anime_release import AnimeRelease


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


class CommonRSSFeedSource(FeedSource):
    """
    Generic feed source for common RSS feeds
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
    ) -> AnimeRelease | None:
        title = getattr(entry, "title", None)
        download_url = self._get_download_url(entry)

        if not download_url or not title:
            logger.debug("Skipping entry without title or download URL")
            return None

        return AnimeRelease(
            title=title,
            download_url=download_url,
            anime_name=None,
            season=None,
            fansub=None,
        )
