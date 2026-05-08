from __future__ import annotations

from typing import Protocol

from openlist_ani.application.anime_library_ingestion.models import (
    ParseResult,
    PipelineContext,
)
from openlist_ani.application.common import OAniEvent
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.domain.download_task.downloader import (
    DownloadedFile,
    DownloaderMemento,
    DownloadRequest,
)
from openlist_ani.domain.download_task.file_renamer import (
    RenamedFile,
    RenameRequest,
)
from openlist_ani.domain.download_task.memento import TaskMemento


class EventPublisherPort(Protocol):
    async def publish(self, event: OAniEvent) -> None: ...


class FeedReaderPort(Protocol):
    async def fetch_new_releases(self) -> list[AnimeRelease]: ...


class MetadataParserPort(Protocol):
    async def parse(self, entries: list[AnimeRelease]) -> list[ParseResult]: ...


class DownloaderPort(Protocol):
    @property
    def downloader_type(self) -> str: ...

    async def download(
        self, context: PipelineContext[DownloadRequest]
    ) -> DownloadedFile: ...


class FileRenamerPort(Protocol):
    async def rename(self, context: PipelineContext[RenameRequest]) -> RenamedFile: ...


class TaskMementoStorePort(Protocol):
    def load_all(self) -> list[TaskMemento]: ...

    def save(self, task_memento: TaskMemento) -> None: ...

    def delete(self, task_id: str) -> None: ...

    def atomic_flush(self) -> None: ...


class TaskRegistryPort(Protocol):
    def get_task(self, task_id: str) -> TaskMemento | None: ...


class ActiveTaskQueryPort(Protocol):
    def list_active_tasks(self) -> list[TaskMemento]: ...


class DownloadTaskReservationPort(Protocol):
    async def reserve_download_task(
        self,
        release: AnimeRelease,
        base_path: str | None = None,
    ) -> TaskMemento | None: ...


class AnimeLibraryRepositoryPort(Protocol):
    async def is_downloaded(self, title: str) -> bool: ...

    async def add_release(self, release: AnimeRelease) -> None: ...

    async def find_releases_by_episode(
        self, anime_name: str, season: int, episode: int
    ) -> list[dict]: ...


class NotifierPort(Protocol):
    async def send_download_complete_notification(
        self, anime_name: str, title: str
    ) -> dict[str, bool]: ...


def empty_downloader_memento(downloader: DownloaderPort) -> DownloaderMemento:
    return DownloaderMemento(downloader.downloader_type, {})
