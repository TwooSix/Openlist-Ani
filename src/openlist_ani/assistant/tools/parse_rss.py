"""
Parse RSS feed tool.
"""

import json
from typing import Any, Dict

from ...core.website import WebsiteFactory
from ...database import db
from ...logger import logger
from .base import BaseTool


class ParseRssTool(BaseTool):
    """Tool for parsing RSS feeds."""

    @property
    def name(self) -> str:
        return "parse_rss"

    @property
    def description(self) -> str:
        return (
            "Parse an RSS feed URL and extract all resource information including titles, "
            "download URLs, and metadata. Use this when user provides an RSS link. "
            "After parsing, use download_resource for each desired resource."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rss_url": {
                    "type": "string",
                    "description": "RSS feed URL to parse",
                }
            },
            "required": ["rss_url"],
        }

    async def execute(self, rss_url: str, **kwargs) -> str:
        """Parse RSS feed and return resource information.

        Args:
            rss_url: RSS feed URL

        Returns:
            JSON string with list of resources
        """
        logger.info(f"Assistant: Parsing RSS feed: {rss_url}")

        try:
            # Get website handler
            factory = WebsiteFactory()
            handler = factory.create(rss_url)
            if not handler:
                return json.dumps({"error": "Unsupported website"})

            # Fetch RSS feed
            entries = await handler.fetch_feed(rss_url)
            if not entries:
                return json.dumps(
                    {"resources": [], "message": "No resources found in RSS feed"}
                )

            logger.info(f"Assistant: Found {len(entries)} entries from RSS")

            # Convert entries to JSON-serializable format and separate downloaded/new
            resources = []
            downloaded_count = 0
            for entry in entries:
                is_downloaded = await db.is_downloaded(entry.title)
                if is_downloaded:
                    downloaded_count += 1
                resources.append(
                    {
                        "title": entry.title,
                        "download_url": entry.download_url or "",
                        "is_downloaded": is_downloaded,
                        "anime_name": entry.anime_name,
                        "episode": entry.episode,
                        "season": entry.season,
                        "quality": entry.quality.value if entry.quality else None,
                        "fansub": entry.fansub,
                    }
                )

            result = {
                "resources": resources,
                "total_count": len(resources),
                "downloaded_count": downloaded_count,
                "new_count": len(resources) - downloaded_count,
            }

            if downloaded_count > 0:
                result["warning"] = (
                    f"⚠️ {downloaded_count} resources are already downloaded and should NOT be downloaded again!"
                )

            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            logger.exception(f"Assistant: Error parsing RSS {rss_url}")
            return json.dumps({"error": str(e)})
