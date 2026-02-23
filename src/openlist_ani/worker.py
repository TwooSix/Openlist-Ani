import asyncio

from .config import config
from .core.download import DownloadManager
from .core.parser.parser import parse_metadata
from .core.rss import RSSManager
from .core.website.model import AnimeResourceInfo
from .logger import logger


async def process_rss_updates(rss: RSSManager, manager: DownloadManager) -> None:
    """Process RSS updates in a continuous loop.

    This function runs indefinitely, checking for RSS updates every 60 seconds
    and starting downloads for new entries. The DownloadManager handles
    concurrency control internally.

    Args:
        rss: RSSManager instance for checking feed updates
        manager: DownloadManager instance for handling downloads
    """
    logger.info("RSS processor started.")

    while True:
        try:
            logger.info("Checking RSS updates...")
            new_entries = await rss.check_update()

            if new_entries:
                logger.info(f"Found {len(new_entries)} new entries from RSS feeds")

                tasks = [_download_entry(manager, entry) for entry in new_entries]
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                logger.info("No new entries found in RSS feeds")

        except Exception:
            logger.exception("Error in RSS processing")

        await asyncio.sleep(config.rss.interval_time)


async def _download_entry(manager: DownloadManager, entry: AnimeResourceInfo) -> None:
    """Download a single anime entry.

    Args:
        manager: DownloadManager instance
        entry: Anime resource information
    """
    try:
        logger.info(f"Parsing: {entry.title}")
        try:
            meta = await parse_metadata(entry)
        except Exception as e:
            logger.error(
                f"Metadata extraction failed for {entry.title}: {e}. Skipping."
            )
            return

        if not meta:
            logger.error(f"Metadata extraction failed for {entry.title}. Skipping.")
            return

        # Update entry with parsed metadata
        entry.anime_name = meta.anime_name
        entry.season = meta.season
        entry.episode = meta.episode
        entry.quality = meta.quality
        entry.fansub = meta.fansub if entry.fansub is None else entry.fansub
        entry.languages = meta.languages
        entry.version = meta.version

        # Safely format season/episode info
        season_str = f"S{meta.season:02d}" if meta.season is not None else "S??"
        episode_str = f"E{meta.episode:02d}" if meta.episode is not None else "E??"
        logger.info(
            f"Parsed metadata: {meta.anime_name} {season_str}{episode_str} - {entry.title}"
        )

        # Start download (completion callback handles database save)
        await manager.download(entry, config.openlist.download_path)

    except Exception:
        logger.exception(f"Error processing {entry.title}")
