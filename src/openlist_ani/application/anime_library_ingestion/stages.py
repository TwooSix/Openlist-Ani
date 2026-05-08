from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from cachetools import TTLCache

from openlist_ani.application.anime_library_ingestion.buffer import PipelineBuffer
from openlist_ani.application.anime_library_ingestion.filters import FilterChain
from openlist_ani.application.anime_library_ingestion.models import (
    DownloadCandidate,
    ParseResult,
    PipelineContext,
)
from openlist_ani.application.anime_library_ingestion.ports import (
    AnimeLibraryRepositoryPort,
    DownloaderPort,
    DownloadTaskReservationPort,
    EventPublisherPort,
    FeedReaderPort,
    FileRenamerPort,
    MetadataParserPort,
    NotifierPort,
    TaskMementoStorePort,
    TaskRegistryPort,
)
from openlist_ani.application.anime_library_ingestion.settings import (
    AnimeLibraryIngestionSettings,
)
from openlist_ani.application.anime_library_ingestion.stage import PipelineStage
from openlist_ani.application.common import OAniEvent, OAniEventType, Severity
from openlist_ani.domain.anime_release import (
    AnimeRelease,
    ReleaseDirectoryPlanner,
    ReleaseFilenamePlanner,
    format_anime_episode,
)
from openlist_ani.domain.download_task.downloader import (
    DownloadedFile,
    DownloadRequest,
)
from openlist_ani.domain.download_task.file_renamer import (
    RenamedFile,
    RenameRequest,
)
from openlist_ani.domain.download_task.memento import PipelineMemento, TaskMemento
from openlist_ani.domain.download_task.task import DownloadState
from openlist_ani.logger import logger

rss_logger = logger
download_logger = logger
rename_logger = logger
notification_logger = logger


def _release_label(release: AnimeRelease) -> str:
    return format_anime_episode(
        release.anime_name,
        release.season,
        release.episode,
    )


class RSSStage(PipelineStage[None]):
    _METADATA_CACHE_MAXSIZE = 8192
    _METADATA_CACHE_TTL = 60 * 60 * 24 * 7

    def __init__(
        self,
        feed_reader: FeedReaderPort,
        metadata_parser: MetadataParserPort,
        anime_library_repository: AnimeLibraryRepositoryPort,
        output_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]],
        event_publisher: EventPublisherPort,
        filter_chain: FilterChain,
        settings: AnimeLibraryIngestionSettings,
        task_reservation: DownloadTaskReservationPort,
        interval_seconds: float | None = None,
    ) -> None:
        super().__init__("rss", None, event_publisher)
        self._feed_reader = feed_reader
        self._metadata_parser = metadata_parser
        self._anime_library_repository = anime_library_repository
        self._output = output_buffer
        self._filter_chain = filter_chain
        self._settings = settings
        self._interval_seconds = interval_seconds
        self._task_reservation = task_reservation
        self._metadata_cache: TTLCache[str, ParseResult] = TTLCache(
            maxsize=self._METADATA_CACHE_MAXSIZE,
            ttl=self._METADATA_CACHE_TTL,
        )

    async def process_item(self, item: None) -> None:
        return None

    async def process_batch(self) -> None:
        try:
            await self._scan_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self.on_error(e)

        await self._sleep_until_next_scan()

    async def _scan_once(self) -> None:
        rss_logger.info("RSS scan started")
        entries = self._deduplicate(await self._feed_reader.fetch_new_releases())
        processable, prefilter_summary = await self._filter_downloaded(entries)
        if not processable:
            self._log_scan_completed(0, prefilter_summary)
            return

        self._log_scan_completed(len(processable), prefilter_summary)
        enriched = await self._parse_and_enrich(processable)
        accepted = await self._filter_candidates(enriched)
        await self._queue_download_candidates(accepted)

    async def _parse_and_enrich(
        self, entries: list[AnimeRelease]
    ) -> list[AnimeRelease]:
        rss_logger.info(f"Metadata parsing started: {len(entries)} release(s)")
        enriched = await self._enrich(entries)
        rss_logger.info(
            f"Metadata parsing completed: {len(enriched)}/{len(entries)} release(s)"
        )
        return enriched

    async def _filter_candidates(
        self, entries: list[AnimeRelease]
    ) -> list[AnimeRelease]:
        rss_logger.info(f"Filtering started: {len(entries)} release(s)")
        accepted = await self._filter_chain.apply(entries)
        filter_summary = self._filter_chain.report_summary(include_details=True)
        summary_suffix = f"; reasons: {filter_summary}" if filter_summary else ""
        rss_logger.info(
            f"Filtering completed: {len(accepted)} accepted, "
            f"{len(entries) - len(accepted)} skipped{summary_suffix}"
        )
        return accepted

    async def _queue_download_candidates(self, entries: list[AnimeRelease]) -> None:
        for entry in entries:
            task = await self._reserve_download_task(entry)
            if task is None:
                rss_logger.debug(
                    f"Skipping RSS entry {entry.title}: already_downloading"
                )
                continue
            await self._output.put(
                PipelineContext(
                    workflow_id=task.task_id,
                    payload=DownloadCandidate(
                        release=task.release,
                        base_path=task.base_path,
                        downloader_memento=task.downloader,
                    ),
                )
            )

    @staticmethod
    def _log_scan_completed(count: int, prefilter_summary: str) -> None:
        scan_suffix = f"; skipped: {prefilter_summary}" if prefilter_summary else ""
        if count:
            rss_logger.info(f"RSS scan completed: {count} new release(s){scan_suffix}")
        else:
            rss_logger.info(f"RSS scan completed: no new releases{scan_suffix}")

    async def _sleep_until_next_scan(self) -> None:
        await asyncio.sleep(
            self._interval_seconds
            if self._interval_seconds is not None
            else self._settings.rss_interval_seconds
        )

    async def _reserve_download_task(self, entry: AnimeRelease) -> TaskMemento | None:
        return await self._task_reservation.reserve_download_task(
            entry,
            self._settings.download_path,
        )

    @staticmethod
    def _deduplicate(entries: list[AnimeRelease]) -> list[AnimeRelease]:
        unique: list[AnimeRelease] = []
        seen: set[str] = set()
        for entry in entries:
            key = entry.download_url or entry.title
            if key in seen:
                rss_logger.debug(f"Skipping duplicate RSS entry: {entry.title}")
                continue
            seen.add(key)
            unique.append(entry)
        return unique

    async def _filter_downloaded(
        self, entries: list[AnimeRelease]
    ) -> tuple[list[AnimeRelease], str]:
        accepted: list[AnimeRelease] = []
        reasons: dict[str, int] = {}
        for entry in entries:
            reason: str | None = None
            if not entry.download_url:
                reason = "missing_url"
            elif await self._anime_library_repository.is_downloaded(entry.title):
                reason = "already_downloaded"

            if reason is None:
                accepted.append(entry)
            else:
                reasons[reason] = reasons.get(reason, 0) + 1
                rss_logger.debug(f"Skipping RSS entry {entry.title}: {reason}")

        summary = ", ".join(
            f"{reason}={count}" for reason, count in sorted(reasons.items())
        )
        return accepted, summary

    async def _enrich(self, entries: list[AnimeRelease]) -> list[AnimeRelease]:
        parsed_results = await self._parse_metadata(entries)
        enriched: list[AnimeRelease] = []
        for entry, result in zip(entries, parsed_results):
            if self._apply_metadata(entry, result):
                enriched.append(entry)
        return enriched

    async def _parse_metadata(self, entries: list[AnimeRelease]) -> list[ParseResult]:
        results_by_key: dict[str, ParseResult] = {}
        missing: list[AnimeRelease] = []
        for entry in entries:
            key = self._metadata_cache_key(entry)
            cached = self._metadata_cache.get(key)
            if cached is None:
                missing.append(entry)
            else:
                results_by_key[key] = cached

        if missing:
            parsed = await self._metadata_parser.parse(missing)
            for entry, result in zip(missing, parsed):
                key = self._metadata_cache_key(entry)
                results_by_key[key] = result
                if result.success and result.result is not None:
                    self._metadata_cache[key] = result

        return [results_by_key[self._metadata_cache_key(entry)] for entry in entries]

    @staticmethod
    def _metadata_cache_key(entry: AnimeRelease) -> str:
        return entry.download_url or entry.title

    @staticmethod
    def _apply_metadata(entry: AnimeRelease, result: ParseResult) -> bool:
        if not result.success or result.result is None:
            rss_logger.warning(
                f"Metadata extraction failed for {entry.title}: {result.error}"
            )
            return False

        meta = result.result
        entry.anime_name = meta.anime_name
        entry.season = meta.season
        entry.episode = meta.episode
        entry.quality = meta.quality
        entry.fansub = meta.fansub if entry.fansub is None else entry.fansub
        entry.languages = meta.languages
        entry.version = meta.version
        return True


class DownloadStage(PipelineStage[PipelineContext[DownloadCandidate]]):
    def __init__(
        self,
        input_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]],
        output_buffer: PipelineBuffer[PipelineContext[DownloadedFile]],
        downloader: DownloaderPort,
        task_store: TaskMementoStorePort,
        event_publisher: EventPublisherPort,
        task_registry: TaskRegistryPort,
        directory_planner: ReleaseDirectoryPlanner,
        worker_count: int = 1,
    ) -> None:
        super().__init__("download", input_buffer, event_publisher, worker_count)
        self._output = output_buffer
        self._downloader = downloader
        self._task_store = task_store
        self._task_registry = task_registry
        self._directory_planner = directory_planner

    async def process_item(self, item: PipelineContext[DownloadCandidate]) -> None:
        memento = await self._resolve_task(item)
        if memento is None:
            return

        try:
            label = _release_label(memento.release)
            memento.state = DownloadState.DOWNLOADING
            memento.started_at = memento.started_at or datetime.now().isoformat()
            memento.pipeline.next_buffer = "download"
            self._task_store.save(memento)
            download_logger.info(f"Download started: {label}")
            await self.event_publisher.publish(
                OAniEvent(OAniEventType.DOWNLOAD_STARTED, {"task_id": memento.task_id})
            )

            async def checkpoint_downloader(downloader_memento) -> None:
                memento.downloader = downloader_memento
                self._task_store.save(memento)
                await asyncio.sleep(0)

            downloaded = await self._downloader.download(
                PipelineContext(
                    workflow_id=item.workflow_id,
                    payload=DownloadRequest(
                        release=memento.release,
                        base_path=memento.base_path,
                        target_directory_path=(
                            self._directory_planner.target_directory_path(
                                memento.base_path,
                                memento.release,
                            )
                        ),
                        downloader_memento=memento.downloader,
                        checkpoint_callback=checkpoint_downloader,
                    ),
                )
            )
            memento.state = DownloadState.DOWNLOADED
            memento.downloader = downloaded.downloader_memento
            memento.pipeline = PipelineMemento(
                next_buffer="rename",
                downloaded_directory_path=downloaded.directory_path,
                downloaded_filename=downloaded.filename,
            )
            memento.output_path = downloaded.path
            self._task_store.save(memento)
            download_logger.info(f"Download completed: {label}")
            await self.event_publisher.publish(
                OAniEvent(
                    OAniEventType.DOWNLOAD_COMPLETED, {"task_id": memento.task_id}
                )
            )
            await self._output.put(
                PipelineContext(workflow_id=item.workflow_id, payload=downloaded)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._retry_or_mark_failed(item, memento, error)

    async def _resolve_task(
        self, item: PipelineContext[DownloadCandidate]
    ) -> TaskMemento | None:
        existing = self._task_registry.get_task(item.workflow_id)
        if existing is not None:
            return existing

        download_logger.warning(
            f"Skipping download candidate without reserved task: {item.workflow_id}"
        )
        return None

    async def _mark_failed(self, memento: TaskMemento, error: Exception) -> None:
        memento.state = DownloadState.FAILED
        memento.retry.last_error = str(error)
        self._task_store.save(memento)
        await self.event_publisher.publish(
            OAniEvent(
                OAniEventType.TASK_FAILED,
                {"task_id": memento.task_id, "error": str(error)},
                Severity.ERROR,
            )
        )

    async def _retry_or_mark_failed(
        self,
        item: PipelineContext[DownloadCandidate],
        memento: TaskMemento,
        error: Exception,
    ) -> None:
        memento.retry.last_error = str(error)
        if memento.retry.retry_count < memento.retry.max_retries:
            memento.retry.retry_count += 1
            memento.state = DownloadState.PENDING
            memento.pipeline.next_buffer = "download"
            self._task_store.save(memento)
            download_logger.warning(
                f"Download failed for {memento.release.title}; "
                f"will retry {memento.retry.retry_count}/{memento.retry.max_retries}; "
                f"task remains queued: {error}"
            )
            if self.input_buffer is not None:
                await self.input_buffer.put(item)
            return

        download_logger.error(
            f"Download failed after {memento.retry.max_retries} retries "
            f"for {memento.release.title}; task marked failed; "
            f"pipeline will continue: {error}"
        )
        await self._mark_failed(memento, error)

    async def on_error(
        self, error: Exception, item: PipelineContext[DownloadCandidate] | None = None
    ) -> None:
        await super().on_error(error, item)


class RenameStage(PipelineStage[PipelineContext[DownloadedFile]]):
    def __init__(
        self,
        input_buffer: PipelineBuffer[PipelineContext[DownloadedFile]],
        output_buffer: PipelineBuffer[PipelineContext[RenamedFile]],
        file_renamer: FileRenamerPort,
        task_store: TaskMementoStorePort,
        event_publisher: EventPublisherPort,
        task_lookup: Callable[[str], TaskMemento | None],
        filename_planner: ReleaseFilenamePlanner,
    ) -> None:
        super().__init__("rename", input_buffer, event_publisher)
        self._output = output_buffer
        self._file_renamer = file_renamer
        self._task_store = task_store
        self._task_lookup = task_lookup
        self._filename_planner = filename_planner

    async def process_item(self, item: PipelineContext[DownloadedFile]) -> None:
        downloaded = item.payload
        memento = self._task_lookup(item.workflow_id)
        if memento is None:
            return

        label = _release_label(downloaded.release)
        memento.state = DownloadState.RENAMING
        memento.pipeline.next_buffer = "rename"
        self._task_store.save(memento)
        target_filename = self._filename_planner.filename(
            downloaded.release, downloaded.filename
        )
        rename_detail = f"{downloaded.filename} -> {target_filename}"
        rename_logger.info(f"Rename started: {label}; {rename_detail}")
        renamed = await self._file_renamer.rename(
            PipelineContext(
                workflow_id=item.workflow_id,
                payload=RenameRequest(
                    release=downloaded.release,
                    directory_path=downloaded.directory_path,
                    source_filename=downloaded.filename,
                    target_filename=target_filename,
                ),
            )
        )

        memento.state = DownloadState.RENAMED
        memento.pipeline.next_buffer = "notification"
        memento.pipeline.renamed_path = renamed.path
        memento.output_path = renamed.path
        self._task_store.save(memento)
        rename_logger.info(f"Rename completed: {label}; {rename_detail}")
        await self.event_publisher.publish(
            OAniEvent(OAniEventType.RENAME_COMPLETED, {"task_id": item.workflow_id})
        )
        await self._output.put(
            PipelineContext(workflow_id=item.workflow_id, payload=renamed)
        )

    async def on_error(
        self, error: Exception, item: PipelineContext[DownloadedFile] | None = None
    ) -> None:
        if item is not None:
            memento = self._task_lookup(item.workflow_id)
            if memento is not None:
                memento.state = DownloadState.FAILED
                memento.retry.last_error = str(error)
                self._task_store.save(memento)
        await super().on_error(error, item)


class NotificationStage(PipelineStage[PipelineContext[RenamedFile]]):
    def __init__(
        self,
        input_buffer: PipelineBuffer[PipelineContext[RenamedFile]],
        notifier: NotifierPort | None,
        task_store: TaskMementoStorePort,
        event_publisher: EventPublisherPort,
        anime_library_repository: AnimeLibraryRepositoryPort,
        task_lookup: Callable[[str], TaskMemento | None],
    ) -> None:
        super().__init__("notification", input_buffer, event_publisher)
        self._notifier = notifier
        self._task_store = task_store
        self._anime_library_repository = anime_library_repository
        self._task_lookup = task_lookup

    async def process_item(self, item: PipelineContext[RenamedFile]) -> None:
        renamed = item.payload
        memento = self._task_lookup(item.workflow_id)
        if memento is None:
            return

        label = _release_label(renamed.release)
        memento.state = DownloadState.NOTIFYING
        memento.pipeline.next_buffer = "notification"
        self._task_store.save(memento)
        notification_logger.info(f"Notification started: {label}")

        await self._anime_library_repository.add_release(renamed.release)
        if self._notifier is not None:
            anime_name = renamed.release.anime_name or "Unknown"
            notification_results = (
                await self._notifier.send_download_complete_notification(
                    anime_name,
                    renamed.release.title,
                )
            )
            self._log_notification_result(label, notification_results)
        else:
            notification_logger.info(f"Notification skipped: {label} (disabled)")

        memento.state = DownloadState.COMPLETED
        memento.completed_at = datetime.now().isoformat()
        memento.output_path = renamed.path
        self._task_store.save(memento)
        self._task_store.delete(item.workflow_id)
        await self.event_publisher.publish(
            OAniEvent(OAniEventType.TASK_COMPLETED, {"task_id": item.workflow_id})
        )

    @staticmethod
    def _log_notification_result(
        label: str,
        results: dict[str, bool],
    ) -> None:
        if not results:
            notification_logger.info(f"Notification queued: {label}")
            return

        failed = [target for target, ok in results.items() if not ok]
        if not failed:
            targets = ", ".join(results)
            notification_logger.info(f"Notification sent: {label} ({targets})")
            return

        notification_logger.warning(
            f"Notification failed: {label}; "
            "download task completed, but notification was not delivered "
            f"via {', '.join(failed)}"
        )

    async def on_error(
        self, error: Exception, item: PipelineContext[RenamedFile] | None = None
    ) -> None:
        if item is not None:
            memento = self._task_lookup(item.workflow_id)
            if memento is not None:
                memento.state = DownloadState.FAILED
                memento.retry.last_error = str(error)
                self._task_store.save(memento)
        await super().on_error(error, item)
