"""HTTP service facade for API schema conversion."""

from __future__ import annotations

from openlist_ani.application.anime_library_ingestion.application_service import (
    AnimeLibraryApplicationService,
)
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.domain.download_task.memento import TaskMemento

from .schema import (
    DownloadTaskResponse,
    ParseRSSEntry,
    ParseRSSResponse,
    ResolveMagnetFile,
    ResolveMagnetResponse,
    ResolveTorrentResponse,
)


def _build_task_response(task: TaskMemento) -> DownloadTaskResponse:
    release = task.release
    return DownloadTaskResponse(
        id=task.task_id,
        title=release.title,
        download_url=release.download_url,
        state=task.state.value,
        anime_name=release.anime_name,
        season=release.season,
        episode=release.episode,
        fansub=release.fansub,
        quality=release.quality.value if release.quality else None,
        error_message=task.retry.last_error,
        retry_count=task.retry.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        save_path=task.base_path,
        final_path=task.output_path,
    )


def _build_parse_entry(index: int, release: AnimeRelease) -> ParseRSSEntry:
    return ParseRSSEntry(
        index=index,
        title=release.title,
        download_url=release.download_url,
        anime_name=release.anime_name,
        episode=release.episode,
        fansub=release.fansub,
        quality=release.quality.value if release.quality else None,
        languages=[lang.value for lang in (release.languages or [])],
    )


def _build_magnet_response(result) -> ResolveMagnetResponse:
    return ResolveMagnetResponse(
        success=result.success,
        message=result.message,
        title=result.title,
        source=result.source,
        file_count=result.file_count,
        files=[ResolveMagnetFile(name=f.name, size=f.size) for f in result.files],
        is_collection=result.is_collection,
        collection_reason=result.collection_reason,
    )


class BackendApiService:
    """Singleton bridge between FastAPI routes and application use cases."""

    _instance: BackendApiService | None = None

    def __init__(self, application_service: AnimeLibraryApplicationService) -> None:
        self._application_service = application_service

    @classmethod
    def init(
        cls, application_service: AnimeLibraryApplicationService
    ) -> BackendApiService:
        cls._instance = cls(application_service)
        return cls._instance

    @classmethod
    def get(cls) -> BackendApiService:
        if cls._instance is None:
            raise RuntimeError("BackendApiService not initialized")
        return cls._instance

    @property
    def pipeline(self):
        return self._application_service.pipeline

    def add_rss_url(self, url: str) -> tuple[bool, str, list[str]]:
        return self._application_service.add_rss_url(url)

    async def create_download(
        self,
        download_url: str,
        title: str,
    ) -> tuple[bool, str, DownloadTaskResponse | None]:
        outcome = await self._application_service.create_download(download_url, title)
        return (
            outcome.success,
            outcome.message,
            _build_task_response(outcome.task) if outcome.task else None,
        )

    def list_downloads(self) -> list[DownloadTaskResponse]:
        return [
            _build_task_response(task)
            for task in self._application_service.list_downloads()
        ]

    def get_download(self, task_id: str) -> DownloadTaskResponse | None:
        task = self._application_service.get_download(task_id)
        return _build_task_response(task) if task else None

    async def parse_rss(
        self,
        url: str,
        limit: int | None = None,
    ) -> ParseRSSResponse:
        outcome = await self._application_service.parse_rss(url, limit)
        entries = outcome.entries or []
        return ParseRSSResponse(
            success=outcome.success,
            message=outcome.message,
            total=outcome.total,
            entries=[
                _build_parse_entry(index, release)
                for index, release in enumerate(entries)
            ],
        )

    async def resolve_magnet(
        self,
        magnet: str,
        metadata_timeout: int = 30,
    ) -> ResolveMagnetResponse:
        result = await self._application_service.resolve_magnet(
            magnet, metadata_timeout=metadata_timeout
        )
        return _build_magnet_response(result)

    async def resolve_torrent(self, url: str) -> ResolveTorrentResponse:
        result = await self._application_service.resolve_torrent(url)
        magnet_response = _build_magnet_response(result)
        return ResolveTorrentResponse(**magnet_response.model_dump())
