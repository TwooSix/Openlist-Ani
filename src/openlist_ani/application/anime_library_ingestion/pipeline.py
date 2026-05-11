from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from openlist_ani.application.anime_library_ingestion.buffer import PipelineBuffer
from openlist_ani.application.anime_library_ingestion.settings import (
    AnimeLibraryIngestionSettings,
)
from openlist_ani.application.anime_library_ingestion.models import (
    DownloadCandidate,
    PipelineContext,
)
from openlist_ani.application.anime_library_ingestion.filters import (
    FilterChain,
    MetadataFilter,
    PriorityFilter,
    RegexTitleFilter,
    StrictRenameFilter,
)
from openlist_ani.application.anime_library_ingestion.ports import (
    DownloaderPort,
    EventPublisherPort,
    FeedReaderPort,
    FileRenamerPort,
    MetadataParserPort,
    MetadataValidatorPort,
    NotifierPort,
    AnimeLibraryRepositoryPort,
    TaskMementoStorePort,
    empty_downloader_memento,
)
from openlist_ani.application.anime_library_ingestion.task_coordinator import (
    TaskCoordinator,
)
from openlist_ani.domain.anime_release import (
    AnimeRelease,
    ReleaseDirectoryPlanner,
    ReleaseFilenamePlanner,
)
from openlist_ani.domain.download_task.downloader import (
    DownloadedFile,
)
from openlist_ani.domain.download_task.file_renamer import (
    RenamedFile,
)
from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import DownloadState
from openlist_ani.logger import logger

from .stages import DownloadStage, NotificationStage, RenameStage, RSSStage

if TYPE_CHECKING:
    from .stages import PipelineStage


pipeline_logger = logger


class AnimeLibraryIngestionPipeline:
    def __init__(
        self,
        downloader: DownloaderPort,
        file_renamer: FileRenamerPort,
        task_store: TaskMementoStorePort,
        event_publisher: EventPublisherPort,
        anime_library_repository: AnimeLibraryRepositoryPort,
        metadata_parser: MetadataParserPort,
        metadata_validator: MetadataValidatorPort,
        settings: AnimeLibraryIngestionSettings,
        feed_reader: FeedReaderPort | None = None,
        notifier: NotifierPort | None = None,
    ) -> None:
        self.downloader = downloader
        self.file_renamer = file_renamer
        self.event_publisher = event_publisher
        self.anime_library_repository = anime_library_repository
        self.metadata_parser = metadata_parser
        self.metadata_validator = metadata_validator
        self.settings = settings
        self.feed_reader = feed_reader
        self.notifier = notifier
        self.task_coordinator = TaskCoordinator(
            task_store=task_store,
            event_publisher=event_publisher,
            default_base_path=settings.download_path,
        )
        self.download_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]] = (
            PipelineBuffer("download")
        )
        self.rename_buffer: PipelineBuffer[PipelineContext[DownloadedFile]] = (
            PipelineBuffer("rename")
        )
        self.notification_buffer: PipelineBuffer[PipelineContext[RenamedFile]] = (
            PipelineBuffer("notification")
        )
        self._stage_tasks: list[asyncio.Task[None]] = []
        self._stages: list[PipelineStage] = []

    async def start(self) -> None:
        loaded_tasks = self.task_coordinator.load_all()
        restore_stats = await self.restore()
        if loaded_tasks:
            pipeline_logger.info(
                "Task restore summary: "
                f"loaded={len(loaded_tasks)}, "
                f"download={restore_stats['download']}, "
                f"rename={restore_stats['rename']}, "
                f"notification={restore_stats['notification']}, "
                f"failed={restore_stats['failed']}, "
                f"skipped={restore_stats['skipped']}"
            )
        self._stages = self._build_stages()
        self._stage_tasks = [
            asyncio.create_task(stage.run())
            for stage in self._stages
            for _ in range(stage.worker_count)
        ]
        pipeline_logger.info("Anime library ingestion pipeline started")

    async def stop(self) -> None:
        for stage in self._stages:
            await stage.stop()
        for task in self._stage_tasks:
            task.cancel()
        if self._stage_tasks:
            await asyncio.gather(*self._stage_tasks, return_exceptions=True)
        self.task_coordinator.atomic_flush()
        pipeline_logger.info("Anime library ingestion pipeline stopped")

    async def restore(self) -> dict[str, int]:
        stats = {
            "download": 0,
            "rename": 0,
            "notification": 0,
            "failed": 0,
            "skipped": 0,
        }
        for task in self.task_coordinator.list_tasks():
            stats[await self._restore_task(task)] += 1
        return stats

    async def _restore_task(self, task: TaskMemento) -> str:
        if task.state in {
            DownloadState.COMPLETED,
            DownloadState.FAILED,
            DownloadState.CANCELLED,
        }:
            self.task_coordinator.delete(task.task_id)
            return "skipped"

        if self._download_retry_limit_reached(task):
            task.state = DownloadState.FAILED
            task.retry.last_error = task.retry.last_error or (
                "Cannot restore download: retry limit reached"
            )
            self.task_coordinator.save(task)
            return "failed"

        if task.state in {DownloadState.PENDING, DownloadState.DOWNLOADING}:
            await self._enqueue_download(task)
            return "download"

        if task.state in {DownloadState.DOWNLOADED, DownloadState.RENAMING}:
            return "rename" if await self._enqueue_rename(task) else "failed"

        if task.state in {DownloadState.RENAMED, DownloadState.NOTIFYING}:
            return (
                "notification" if await self._enqueue_notification(task) else "failed"
            )

        return "skipped"

    @staticmethod
    def _download_retry_limit_reached(task: TaskMemento) -> bool:
        return (
            task.state in {DownloadState.PENDING, DownloadState.DOWNLOADING}
            and task.retry.retry_count >= task.retry.max_retries
        )

    async def submit_download(
        self,
        release: AnimeRelease,
        base_path: str | None = None,
    ) -> TaskMemento | None:
        task = await self.task_coordinator.reserve_download_task(release, base_path)
        if task is None:
            return None
        await self._enqueue_download(task)
        return task

    def _build_stages(self) -> list[PipelineStage]:
        stages = [
            DownloadStage(
                self.download_buffer,
                self.rename_buffer,
                self.downloader,
                self.task_coordinator,
                self.event_publisher,
                self.task_coordinator,
                ReleaseDirectoryPlanner(),
                self.settings.download_concurrency,
            ),
            RenameStage(
                self.rename_buffer,
                self.notification_buffer,
                self.file_renamer,
                self.task_coordinator,
                self.event_publisher,
                self.task_coordinator.get_task,
                ReleaseFilenamePlanner(self.settings.rename_format),
            ),
            NotificationStage(
                self.notification_buffer,
                self.notifier,
                self.task_coordinator,
                self.event_publisher,
                self.anime_library_repository,
                self.task_coordinator.get_task,
            ),
        ]
        if self.feed_reader is not None:
            stages.insert(
                0,
                RSSStage(
                    self.feed_reader,
                    self.metadata_parser,
                    self.metadata_validator,
                    self.anime_library_repository,
                    self.download_buffer,
                    self.event_publisher,
                    self._build_filter_chain(),
                    self.settings,
                    task_reservation=self.task_coordinator,
                ),
            )
        return stages

    def _build_filter_chain(self) -> FilterChain:
        filters = [
            RegexTitleFilter(self.settings.metadata_filter.exclude_patterns),
            MetadataFilter(self.settings.metadata_filter),
            PriorityFilter(
                self.settings.priority,
                self.anime_library_repository,
                active_task_query=self.task_coordinator,
            ),
        ]
        if self.settings.strict_filtering:
            filters.append(
                StrictRenameFilter(
                    self.settings.rename_format,
                    self.anime_library_repository,
                    active_task_query=self.task_coordinator,
                    base_path=self.settings.download_path,
                )
            )
        return FilterChain(filters)

    async def _enqueue_download(self, task: TaskMemento) -> None:
        await self.download_buffer.put(
            PipelineContext(
                workflow_id=task.task_id,
                payload=DownloadCandidate(
                    release=task.release,
                    base_path=task.base_path,
                    downloader_memento=task.downloader,
                ),
            )
        )

    async def _enqueue_rename(self, task: TaskMemento) -> bool:
        if not (
            task.pipeline.downloaded_directory_path
            and task.pipeline.downloaded_filename
        ):
            task.state = DownloadState.FAILED
            task.retry.last_error = "Cannot restore rename: missing downloaded file"
            self.task_coordinator.save(task)
            pipeline_logger.warning(
                f"Cannot restore task {task.task_id}: {task.release.title}; "
                "missing downloaded file; marked failed"
            )
            return False
        await self.rename_buffer.put(
            PipelineContext(
                workflow_id=task.task_id,
                payload=DownloadedFile(
                    release=task.release,
                    directory_path=task.pipeline.downloaded_directory_path,
                    filename=task.pipeline.downloaded_filename,
                    downloader_memento=task.downloader
                    or empty_downloader_memento(self.downloader),
                ),
            )
        )
        return True

    async def _enqueue_notification(self, task: TaskMemento) -> bool:
        if not task.pipeline.renamed_path:
            task.state = DownloadState.FAILED
            task.retry.last_error = "Cannot restore notification: missing renamed path"
            self.task_coordinator.save(task)
            pipeline_logger.warning(
                f"Cannot restore task {task.task_id}: {task.release.title}; "
                "missing renamed path; marked failed"
            )
            return False
        path = PurePosixPath(task.pipeline.renamed_path)
        await self.notification_buffer.put(
            PipelineContext(
                workflow_id=task.task_id,
                payload=RenamedFile(
                    release=task.release,
                    directory_path=str(path.parent),
                    filename=path.name,
                ),
            )
        )
        return True
