"""Download resource script — download anime via backend API."""

from __future__ import annotations

import logging

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config
from openlist_ani.database import db

logger = logging.getLogger(__name__)


async def run(download_url: str, title: str) -> str:
    """Download a single anime resource via the backend API.

    Args:
        download_url: Download URL (magnet/torrent).
        title: Resource title for identification.

    Returns:
        Result message.
    """
    logger.info(f"Attempting to download resource: {title}")

    try:
        is_downloaded = await db.is_downloaded(title)
        if is_downloaded:
            logger.warning(f"Resource already downloaded, skipping: {title}")
            return f"✅ Already downloaded (skipped): {title}"

        client = BackendClient(config.backend_url)
        try:
            result = await client.create_download(
                download_url=download_url,
                title=title,
            )
        finally:
            await client.close()

        if result.get("success"):
            task = result.get("task", {})
            msg = f"✅ Download submitted: {title}"
            anime_name = task.get("anime_name")
            season = task.get("season")
            episode = task.get("episode")
            if anime_name and season is not None and episode is not None:
                msg += f" ({anime_name} S{season:02d}E{episode:02d})"
            logger.info(f"Download task created for {title}")
            return msg

        message = result.get("message", "Unknown error")
        logger.warning(f"Download rejected: {message}")
        return f"⚠️ {message}"

    except Exception as e:
        logger.exception("Error creating download task")
        return f"❌ Error: {e!s}"


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Download an anime resource")
    parser.add_argument(
        "--download_url", type=str, required=True, help="Download URL (magnet/torrent)"
    )
    parser.add_argument("--title", type=str, required=True, help="Resource title")
    args = parser.parse_args()

    async def _main() -> None:
        await db.init()
        result = await run(download_url=args.download_url, title=args.title)
        print(result)

    asyncio.run(_main())
