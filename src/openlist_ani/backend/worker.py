"""
Background workers for RSS polling and download dispatching.

These workers run as asyncio tasks alongside the FastAPI server
within the backend process.
"""

import asyncio
import time

from cachetools import TTLCache

from ..config import config
from ..core.download import DownloadManager
from ..core.parser.model import ParseResult
from ..core.parser.parser import parse_metadata
from ..core.rss import RSSManager
from ..core.rss.priority import ResourcePriorityFilter
from ..core.website.model import AnimeResourceInfo
from ..database import db
from ..logger import logger


async def poll_rss_feeds(
    rss: RSSManager,
    rss_entry_queue: asyncio.Queue[AnimeResourceInfo],
) -> None:
    """Poll RSS updates continuously and enqueue new entries."""
    logger.info("RSS poll worker started.")

    # Skip cache state — local to this worker, no globals needed.
    priority_filter = ResourcePriorityFilter()
    skip_cache: TTLCache[str, float] = TTLCache(
        maxsize=8192, ttl=60 * 60 * 24 * 7
    )  # 7 days
    last_priority = config.rss.priority.model_copy(deep=True)

    while True:
        try:
            # Detect priority config changes and rebuild caches.
            current_priority = config.rss.priority
            if current_priority != last_priority:
                logger.info(
                    "Priority config changed, clearing skip cache "
                    f"({len(skip_cache)} entries)"
                )
                skip_cache.clear()
                last_priority = current_priority.model_copy(deep=True)

            logger.info("Checking RSS updates...")
            new_entries = await rss.check_update()
            # Filter out entries recently skipped or failed to parse.
            fresh_entries = [e for e in new_entries if e.title not in skip_cache]

            if fresh_entries:
                logger.info(f"Fetched {len(new_entries)} entries from RSS feeds")
                await _process_fresh_entries(
                    fresh_entries, priority_filter, skip_cache, rss_entry_queue
                )
            else:
                logger.info("No new entries found in RSS feeds")
        except Exception:
            logger.exception("Error in RSS poll worker")

        await asyncio.sleep(config.rss.interval_time)


async def _process_fresh_entries(
    fresh_entries: list[AnimeResourceInfo],
    priority_filter: ResourcePriorityFilter,
    skip_cache: TTLCache[str, float],
    rss_entry_queue: asyncio.Queue[AnimeResourceInfo],
) -> None:
    """Parse, enrich, filter and enqueue fresh RSS entries."""
    parsed_results = await parse_metadata(fresh_entries)
    # Apply metadata from parse results.
    enriched: list[AnimeResourceInfo] = []
    for entry, parse_result in zip(fresh_entries, parsed_results):
        if _apply_metadata(entry, parse_result):
            enriched.append(entry)

    # Priority filtering: skip resources dominated by already-downloaded ones.
    filtered = await priority_filter.filter_batch(enriched)

    # Record entries that were enriched but rejected by priority filter.
    filtered_titles = {e.title for e in filtered}
    now = time.monotonic()
    priority_skipped = [e for e in enriched if e.title not in filtered_titles]
    for entry in priority_skipped:
        skip_cache[entry.title] = now

    if filtered:
        logger.info(f"Found {len(filtered)} new entries from RSS feeds")
        for entry in filtered:
            await rss_entry_queue.put(entry)
            # pre-insert into DB to prevent duplicates in next RSS polls
            # it should be auto remove when download fails
            await db.add_resource(entry)


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

        for entry in batch:
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

    logger.info(f"Parsed: {entry!r}")
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
