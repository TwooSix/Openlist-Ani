"""OpenList downloader facade."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from openlist_ani.application.anime_library_ingestion.models import PipelineContext
from openlist_ani.domain.download_task.downloader import (
    DownloadedFile,
    DownloaderMemento,
    DownloadError,
    DownloadRequest,
)
from openlist_ani.domain.download_task.task import DownloadTask
from openlist_ani.integrations.openlist import (
    OfflineDownloadTool,
    OpenListClient,
    OpenListFileConflictResolver,
    normalize_offline_download_tool_name,
)

from .file_detection import OpenListFileDetector
from .workflow import OpenListDownloadWorkflow


class OpenListDownloader:
    """Facade adapting the OpenList private workflow to downloader port methods."""

    def __init__(
        self,
        client: OpenListClient,
        offline_download_tool: OfflineDownloadTool | str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if client is None:
            raise ValueError("client is required")
        if offline_download_tool is None:
            raise ValueError("offline_download_tool is required")

        self._offline_download_tool = normalize_offline_download_tool_name(
            offline_download_tool
        )
        self._client = client
        self._sleep = sleep

    @property
    def downloader_type(self) -> str:
        return "openlist"

    async def download(
        self, context: PipelineContext[DownloadRequest]
    ) -> DownloadedFile:
        request = context.payload
        task = self._task_from_request(context.workflow_id, request)
        workflow = self._workflow()
        try:
            await workflow.run(task, checkpoint=self._checkpoint(request))
        except asyncio.CancelledError:
            raise
        except DownloadError:
            raise
        except Exception as e:
            raise DownloadError(str(e)) from e

        return DownloadedFile(
            release=task.release,
            directory_path=task.downloader_data["materialized_directory_path"],
            filename=task.downloader_data["materialized_filename"],
            downloader_memento=self._memento(task),
        )

    def _workflow(self) -> OpenListDownloadWorkflow:
        return OpenListDownloadWorkflow(
            client=self._client,
            offline_download_tool=self._offline_download_tool,
            file_detector=OpenListFileDetector(self._client, self._sleep),
            conflict_resolver=OpenListFileConflictResolver(self._client, self._sleep),
            sleep=self._sleep,
        )

    def _task_from_request(
        self, workflow_id: str, request: DownloadRequest
    ) -> DownloadTask:
        payload = {}
        if request.downloader_memento is not None:
            payload.update(request.downloader_memento.payload)
        return DownloadTask(
            id=workflow_id,
            release=request.release,
            base_path=request.base_path,
            target_directory_path=request.target_directory_path,
            downloader_data=payload,
        )

    def _checkpoint(
        self, request: DownloadRequest
    ) -> Callable[[DownloadTask], Awaitable[None]] | None:
        if request.checkpoint_callback is None:
            return None

        async def checkpoint(task: DownloadTask) -> None:
            await request.checkpoint_callback(self._memento(task))

        return checkpoint

    def _memento(self, task: DownloadTask) -> DownloaderMemento:
        return DownloaderMemento(
            downloader_type=self.downloader_type,
            payload=dict(task.downloader_data),
        )


__all__ = ["OpenListDownloader"]
