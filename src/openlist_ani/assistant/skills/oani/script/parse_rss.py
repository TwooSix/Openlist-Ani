"""Parse RSS feed script — extract resources from RSS feed URLs."""

from __future__ import annotations

import json
import logging

from openlist_ani.core.website import WebsiteFactory
from openlist_ani.database import db

logger = logging.getLogger(__name__)


async def run(rss_url: str) -> str:
    """Parse RSS feed and return resource information.

    Args:
        rss_url: RSS feed URL to parse.

    Returns:
        JSON string with list of resources.
    """
    logger.info(f"Parsing RSS feed: {rss_url}")

    try:
        factory = WebsiteFactory()
        handler = factory.create(rss_url)
        if not handler:
            return json.dumps({"error": "Unsupported website"})

        entries = await handler.fetch_feed(rss_url)
        if not entries:
            return json.dumps(
                {"resources": [], "message": "No resources found in RSS feed"}
            )

        logger.info(f"Found {len(entries)} entries from RSS")

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

        result: dict = {
            "resources": resources,
            "total_count": len(resources),
            "downloaded_count": downloaded_count,
            "new_count": len(resources) - downloaded_count,
        }

        if downloaded_count > 0:
            result["warning"] = (
                f"⚠️ {downloaded_count} resources are already downloaded "
                "and should NOT be downloaded again!"
            )

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.exception(f"Error parsing RSS {rss_url}")
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Parse an RSS feed")
    parser.add_argument(
        "--rss_url", type=str, required=True, help="RSS feed URL to parse"
    )
    args = parser.parse_args()

    async def _main() -> None:
        await db.init()
        result = await run(rss_url=args.rss_url)
        print(result)

    asyncio.run(_main())
