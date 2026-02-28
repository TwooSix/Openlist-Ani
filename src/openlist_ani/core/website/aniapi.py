import aiohttp

from ...logger import logger
from .base import WebsiteBase
from .model import AnimeResourceInfo


class AniapiWebsite(WebsiteBase):
    """
    Parser for api.ani.rip RSS feed.

    ANi API provides direct download links through the <link> element
    instead of using enclosures. The links point directly to .mp4 files.
    """

    def _get_download_url(self, entry) -> str | None:
        """Extract download link from link attribute.

        ANi API uses direct links to .mp4 files in the <link> element.
        """
        # Try link attribute first (ANi API specific)
        link = getattr(entry, "link", "")
        if link:
            return link

        # Fallback to standard enclosure handling
        for enclosure in entry.get("enclosures", []):
            href = enclosure.get("href", "")
            if href:
                return href

        return None

    async def parse_entry(
        self, entry, session: aiohttp.ClientSession
    ) -> AnimeResourceInfo | None:
        """Parse ANi API RSS entry.

        ANi API entries contain ANi-formatted titles that include metadata.
        We preserve the title for downstream parsing.

        Args:
            entry: feedparser entry object
            session: Active aiohttp session (not used)

        Returns:
            AnimeResourceInfo with title and download_url, or None if invalid
        """
        title = getattr(entry, "title", None)
        download_url = self._get_download_url(entry)

        if not download_url or not title:
            logger.debug("Skipping ANi entry without title or download URL")
            return None

        # ANi titles are in format: [ANi] Title - Episode [Resolution][Source][...][CHT/CHS]
        # Detailed parsing will be done by the parser module
        return AnimeResourceInfo(
            title=title,
            download_url=download_url,
            anime_name=None,
            season=None,
            fansub="ANi",  # ANi is always the fansub for this source
        )
