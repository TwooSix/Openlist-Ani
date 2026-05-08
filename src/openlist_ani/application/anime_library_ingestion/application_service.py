"""HTTP-facing application service for anime library operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from openlist_ani.application.anime_library_ingestion.pipeline import (
    AnimeLibraryIngestionPipeline,
)
from openlist_ani.application.anime_library_ingestion.ports import (
    AnimeLibraryRepositoryPort,
    MetadataParserPort,
)
from openlist_ani.application.anime_library_ingestion.settings import (
    AnimeLibraryIngestionSettings,
)
from openlist_ani.application.anime_library_ingestion.models import ParseResult
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import DownloadState
from openlist_ani.logger import logger


class ReleaseFeedSourcePort(Protocol):
    async def fetch_feed(self, url: str) -> list[AnimeRelease]: ...


class ReleaseFeedSourceFactoryPort(Protocol):
    def create(self, url: str) -> ReleaseFeedSourcePort: ...


ResolveMagnet = Callable[..., Awaitable[Any]]
ResolveTorrent = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class CreateDownloadOutcome:
    success: bool
    message: str
    task: TaskMemento | None = None


@dataclass(frozen=True)
class ParseRSSOutcome:
    success: bool
    message: str
    total: int = 0
    entries: list[AnimeRelease] | None = None


class AnimeLibraryApplicationService:
    """Application facade for HTTP-facing anime library operations."""

    def __init__(
        self,
        pipeline: AnimeLibraryIngestionPipeline,
        metadata_parser: MetadataParserPort,
        anime_library_repository: AnimeLibraryRepositoryPort,
        settings: AnimeLibraryIngestionSettings,
        feed_factory: ReleaseFeedSourceFactoryPort,
        resolve_magnet_func: ResolveMagnet,
        resolve_torrent_func: ResolveTorrent,
        get_rss_urls: Callable[[], list[str]],
        add_rss_url_func: Callable[[str], None],
    ) -> None:
        self._pipeline = pipeline
        self._metadata_parser = metadata_parser
        self._anime_library_repository = anime_library_repository
        self._settings = settings
        self._feed_factory = feed_factory
        self._resolve_magnet = resolve_magnet_func
        self._resolve_torrent = resolve_torrent_func
        self._get_rss_urls = get_rss_urls
        self._add_rss_url = add_rss_url_func

    @property
    def pipeline(self) -> AnimeLibraryIngestionPipeline:
        return self._pipeline

    def add_rss_url(self, url: str) -> tuple[bool, str, list[str]]:
        current_urls = self._get_rss_urls()
        if url in current_urls:
            return False, f"URL already exists: {url}", current_urls

        self._add_rss_url(url)
        updated_urls = self._get_rss_urls()
        logger.info(f"Added RSS URL: {url}")
        return True, f"RSS URL added successfully: {url}", updated_urls

    async def create_download(
        self,
        download_url: str,
        title: str,
    ) -> CreateDownloadOutcome:
        if await self._anime_library_repository.is_downloaded(title):
            logger.info(f"Release already downloaded, skipping: {title}")
            return CreateDownloadOutcome(False, f"Already downloaded: {title}")

        release = AnimeRelease(title=title, download_url=download_url)
        if self._pipeline.task_coordinator.is_downloading(release):
            return CreateDownloadOutcome(False, f"Already downloading: {title}")

        await self._enrich_release(release)
        task = await self._pipeline.submit_download(
            release, self._settings.download_path
        )
        if task is None:
            return CreateDownloadOutcome(False, f"Already downloading: {title}")

        logger.debug(f"Download task created: {title} (id={task.task_id})")
        return CreateDownloadOutcome(True, f"Download started: {title}", task)

    def list_downloads(self) -> list[TaskMemento]:
        return [
            task
            for task in self._pipeline.task_coordinator.list_tasks()
            if task.state
            not in {
                DownloadState.COMPLETED,
                DownloadState.CANCELLED,
            }
        ]

    def get_download(self, task_id: str) -> TaskMemento | None:
        return self._pipeline.task_coordinator.get_task(task_id)

    async def parse_rss(
        self,
        url: str,
        limit: int | None = None,
    ) -> ParseRSSOutcome:
        if not url:
            return ParseRSSOutcome(success=False, message="'url' is required.")

        try:
            feed_source = self._feed_factory.create(url)
        except ValueError as e:
            return ParseRSSOutcome(
                success=False, message=f"Cannot pick feed source for URL: {e}"
            )

        try:
            entries = await feed_source.fetch_feed(url)
        except Exception as e:
            logger.warning(f"parse_rss: feed fetch failed for {url}: {e}")
            return ParseRSSOutcome(success=False, message=f"Failed to fetch RSS: {e}")

        total = len(entries)
        if limit is not None and limit > 0:
            entries = entries[:limit]

        message = (
            f"Parsed {len(entries)} of {total} entries"
            if limit and total > len(entries)
            else f"Parsed {len(entries)} entries"
        )
        return ParseRSSOutcome(
            success=True,
            message=message,
            total=total,
            entries=entries,
        )

    async def resolve_magnet(self, magnet: str, metadata_timeout: int = 30) -> Any:
        return await self._resolve_magnet(magnet, metadata_timeout=metadata_timeout)

    async def resolve_torrent(self, url: str) -> Any:
        return await self._resolve_torrent(url)

    async def _enrich_release(self, release: AnimeRelease) -> None:
        try:
            parse_results = await self._metadata_parser.parse([release])
            parse_result: ParseResult = parse_results[0]
            if parse_result.success and parse_result.result:
                meta = parse_result.result
                release.anime_name = meta.anime_name
                release.season = meta.season
                release.episode = meta.episode
                release.quality = meta.quality
                release.fansub = meta.fansub
                release.languages = meta.languages
                release.version = meta.version
        except Exception as e:
            logger.warning(f"Metadata parsing failed for {release.title}: {e}")
