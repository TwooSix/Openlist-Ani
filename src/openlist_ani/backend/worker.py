import asyncio
import time

from cachetools import TTLCache

from ..config import config
from ..core.download import DownloadManager
from ..core.parser.model import ParseResult
from ..core.parser.parser import parse_metadata
from ..core.rss import RSSManager
from ..core.rss.filter import (
    FilterChain,
    MetadataFilter,
    PriorityFilter,
    RegexTitleFilter,
    StrictRenameFilter,
)
from ..core.website.model import AnimeResourceInfo
from ..database import db
from ..logger import logger


class RSSPollWorker:
    """Continuously poll RSS feeds, parse & filter entries, then enqueue for download.

    The ``run()`` method is the pipeline entry point — each private method
    corresponds to exactly one stage, making the data flow self-documenting.
    """

    _SKIP_CACHE_MAXSIZE = 8192
    _SKIP_CACHE_TTL = 60 * 60 * 24 * 7  # 7 days

    def __init__(
        self,
        rss: RSSManager,
        queue: asyncio.Queue[AnimeResourceInfo],
    ) -> None:
        self._rss = rss
        self._queue = queue
        self._skip_cache: TTLCache[str, float] = TTLCache(
            maxsize=self._SKIP_CACHE_MAXSIZE, ttl=self._SKIP_CACHE_TTL
        )
        self._last_priority = config.rss.priority.model_copy(deep=True)
        self._last_strict: bool = config.rss.strict
        self._last_rename_format: str = config.openlist.rename_format
        self._last_filter_cfg = config.rss.filter.model_copy(deep=True)
        self._filter_chain: FilterChain = self._build_filter_chain()

    # ── pipeline entry point ─────────────────────────────────────────

    async def run(self) -> None:
        """Main loop: fetch → parse → filter → enqueue."""
        logger.info("RSS poll worker started.")

        while True:
            try:
                self._detect_config_changes()

                entries = await self._fetch_new_entries()
                fresh = self._exclude_recently_skipped(entries)

                if fresh:
                    logger.info(f"Fetched {len(entries)} entries from RSS feeds")
                    enriched = await self._enrich_by_parser(fresh)
                    filtered = await self._filter_chain.apply(enriched)
                    self._cache_skipped_entries(enriched, filtered)
                    await self._enqueue_accepted_entries(filtered)
                else:
                    logger.info("No new entries found in RSS feeds")
            except Exception:
                logger.exception("Error in RSS poll worker")

            await asyncio.sleep(config.rss.interval_time)

    # ── pipeline stages ──────────────────────────────────────────────

    def _detect_config_changes(self) -> None:
        """Rebuild filter chain and clear skip cache on config changes."""
        current_priority = config.rss.priority
        current_strict = config.rss.strict
        current_rename_fmt = config.openlist.rename_format
        current_filter_cfg = config.rss.filter

        changed = False
        if current_priority != self._last_priority:
            logger.info(
                "Priority config changed, clearing skip cache "
                f"({len(self._skip_cache)} entries)"
            )
            changed = True
        if current_strict != self._last_strict:
            logger.info(f"Strict mode changed to {current_strict}")
            changed = True
        if current_rename_fmt != self._last_rename_format:
            logger.info("Rename format changed")
            changed = True
        if current_filter_cfg != self._last_filter_cfg:
            logger.info("Filter config changed")
            changed = True

        if changed:
            self._skip_cache.clear()
            self._last_priority = current_priority.model_copy(deep=True)
            self._last_strict = current_strict
            self._last_rename_format = current_rename_fmt
            self._last_filter_cfg = current_filter_cfg.model_copy(deep=True)
            self._filter_chain = self._build_filter_chain()

    def _build_filter_chain(self) -> FilterChain:
        """Construct the filter chain based on current config."""
        chain = FilterChain()
        chain.add_filter(RegexTitleFilter())
        chain.add_filter(MetadataFilter())
        chain.add_filter(PriorityFilter())
        if config.rss.strict:
            chain.add_filter(StrictRenameFilter())
        return chain

    async def _fetch_new_entries(self) -> list[AnimeResourceInfo]:
        """Fetch latest entries from all configured RSS feeds."""
        logger.info("Checking RSS updates...")
        return await self._rss.check_update()

    def _exclude_recently_skipped(
        self, entries: list[AnimeResourceInfo]
    ) -> list[AnimeResourceInfo]:
        """Remove entries that recently failed parsing or were skipped by priority."""
        return [e for e in entries if e.title not in self._skip_cache]

    async def _enrich_by_parser(
        self, entries: list[AnimeResourceInfo]
    ) -> list[AnimeResourceInfo]:
        """Run LLM-based metadata extraction and apply results to entries.

        Entries that fail parsing are silently dropped (logged as errors).
        Website-provided fansub takes precedence over LLM-extracted fansub.
        """
        parsed_results = await parse_metadata(entries)

        enriched: list[AnimeResourceInfo] = []
        for entry, result in zip(entries, parsed_results):
            if self._apply_metadata(entry, result):
                enriched.append(entry)
        return enriched

    def _cache_skipped_entries(
        self,
        enriched: list[AnimeResourceInfo],
        filtered: list[AnimeResourceInfo],
    ) -> None:
        """Record rejected entries in skip cache to avoid re-processing."""
        filtered_titles = {e.title for e in filtered}
        now = time.monotonic()
        for entry in enriched:
            if entry.title not in filtered_titles:
                self._skip_cache[entry.title] = now

    async def _enqueue_accepted_entries(self, entries: list[AnimeResourceInfo]) -> None:
        """Push accepted entries into the download queue and pre-insert into DB."""
        if not entries:
            return
        logger.info(f"Enqueuing {len(entries)} entries for download")
        for entry in entries:
            await self._queue.put(entry)
            # Pre-insert to prevent duplicates in the next RSS poll cycle;
            # the record is auto-removed on download failure.
            await db.add_resource(entry)

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _apply_metadata(
        entry: AnimeResourceInfo,
        parse_result: ParseResult,
    ) -> bool:
        """Write parse result fields onto *entry*. Return True on success."""
        if not parse_result.success:
            logger.error(
                f"Metadata extraction failed for {entry.title}: "
                f"{parse_result.error}. Skipping."
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


class DownloadDispatcher:
    """Drain the entry queue and spawn an asyncio download task per entry.

    The ``run()`` method is the pipeline entry point — collect a batch,
    filter out duplicates, then dispatch each remaining entry.
    """

    def __init__(
        self,
        manager: DownloadManager,
        queue: asyncio.Queue[AnimeResourceInfo],
        active_downloads: set[asyncio.Task[None]],
    ) -> None:
        self._manager = manager
        self._queue = queue
        self._active_downloads = active_downloads

    # ── pipeline entry point ─────────────────────────────────────────

    async def run(self) -> None:
        """Main loop: collect → filter → dispatch."""
        logger.info("Download dispatch worker started (batch mode).")

        while True:
            resources = await self._collect_resources()
            resources_filter = self._filter_downloading(resources)
            if not resources_filter:
                continue
            for entry in resources_filter:
                self._spawn_download(entry)

    # ── pipeline stages ──────────────────────────────────────────────

    async def _collect_resources(self) -> list[AnimeResourceInfo]:
        """Block until at least one entry arrives, then drain whatever else is ready."""
        first = await self._queue.get()
        resources: list[AnimeResourceInfo] = [first]
        while not self._queue.empty():
            resources.append(self._queue.get_nowait())
        return resources

    def _filter_downloading(
        self, batch: list[AnimeResourceInfo]
    ) -> list[AnimeResourceInfo]:
        """Remove entries whose downloads are already in progress."""
        return [e for e in batch if not self._manager.is_downloading(e)]

    def _spawn_download(self, entry: AnimeResourceInfo) -> None:
        """Create an asyncio task for a single download and track it."""
        task = asyncio.create_task(self._execute_download(entry))
        self._active_downloads.add(task)
        task.add_done_callback(lambda t: self._active_downloads.discard(t))

    async def _execute_download(self, entry: AnimeResourceInfo) -> None:
        """Execute a single download and log errors."""
        try:
            await self._manager.download(entry, config.openlist.download_path)
        except Exception:
            logger.exception(f"Error downloading {entry.title}")
