"""
Background workers for RSS polling and download dispatching.

These workers run as asyncio tasks alongside the FastAPI server
within the backend process.
"""

import asyncio

from ..config import config
from ..core.download import DownloadManager
from ..core.parser.model import ParseResult
from ..core.parser.parser import parse_metadata
from ..core.rss import RSSManager
from ..core.rss.priority import ResourcePriorityFilter
from ..core.website.model import AnimeResourceInfo
from ..logger import logger

_priority_filter = ResourcePriorityFilter()


async def poll_rss_feeds(
    rss: RSSManager,
    rss_entry_queue: asyncio.Queue[AnimeResourceInfo],
) -> None:
    """Poll RSS updates continuously and enqueue new entries."""
    logger.info("RSS poll worker started.")

    while True:
        try:
            logger.info("Checking RSS updates...")
            new_entries = await rss.check_update()

            if new_entries:
                logger.info(f"Found {len(new_entries)} new entries from RSS feeds")
                for entry in new_entries:
                    await rss_entry_queue.put(entry)
            else:
                logger.info("No new entries found in RSS feeds")
        except Exception:
            logger.exception("Error in RSS poll worker")

        await asyncio.sleep(config.rss.interval_time)


async def dispatch_downloads(
    manager: DownloadManager,
    rss_entry_queue: asyncio.Queue[AnimeResourceInfo],
    active_downloads: set[asyncio.Task[None]],
) -> None:
    """Dispatch queued entries to background download tasks using batch parsing."""
    logger.info("Download dispatch worker started (batch mode).")

    while True:
        entry = await rss_entry_queue.get()
        batch: list[AnimeResourceInfo] = [entry]

        while not rss_entry_queue.empty():
            batch.append(rss_entry_queue.get_nowait())

        batch = [e for e in batch if not manager.is_downloading(e)]
        if not batch:
            continue

        logger.info(f"Batch parsing {len(batch)} entries...")
        parsed_results = await parse_metadata(batch)

        # Apply metadata from parse results.
        enriched: list[AnimeResourceInfo] = []
        for entry, parse_result in zip(batch, parsed_results):
            if _apply_metadata(entry, parse_result):
                enriched.append(entry)

        if not enriched:
            continue

        # Priority filtering: skip resources dominated by already-downloaded ones.
        filtered = await _priority_filter.filter_batch(enriched)

        for entry in filtered:
            _schedule_download(manager, entry, active_downloads)


def _apply_metadata(
    entry: AnimeResourceInfo,
    parse_result: ParseResult,
) -> bool:
    """Apply parse result to entry. Return True if successful."""
    if not parse_result.success:
        logger.error(
            f"Metadata extraction failed for {entry.title}: {parse_result.error}. Skipping."
        )
        return False

    meta = parse_result.result
    entry.anime_name = meta.anime_name
    entry.season = meta.season
    entry.episode = meta.episode
    entry.quality = meta.quality
    entry.fansub = meta.fansub if entry.fansub is None else entry.fansub
    entry.languages = meta.languages
    entry.version = meta.version

    season_str = f"S{meta.season:02d}" if meta.season is not None else "S??"
    episode_str = f"E{meta.episode:02d}" if meta.episode is not None else "E??"
    logger.info(f"Parsed: {meta.anime_name} {season_str}{episode_str} - {entry.title}")
    return True


def _schedule_download(
    manager: DownloadManager,
    entry: AnimeResourceInfo,
    active_downloads: set[asyncio.Task[None]],
) -> None:
    """Create an asyncio.Task for downloading *entry*."""
    download_task = asyncio.create_task(_download_entry(manager, entry))
    active_downloads.add(download_task)
    download_task.add_done_callback(lambda t: active_downloads.discard(t))


async def _download_entry(manager: DownloadManager, entry: AnimeResourceInfo) -> None:
    """Execute a single download and log errors."""
    try:
        await manager.download(entry, config.openlist.download_path)
    except Exception:
        logger.exception(f"Error downloading {entry.title}")
