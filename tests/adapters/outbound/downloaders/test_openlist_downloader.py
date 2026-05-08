import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openlist_ani.application.anime_library_ingestion.models import PipelineContext
from openlist_ani.adapters.outbound.downloaders import OpenListDownloader
from openlist_ani.domain.anime_release import (
    AnimeRelease,
    LanguageType,
    VideoQuality,
)
from openlist_ani.domain.download_task.downloader import (
    DownloadError,
    DownloaderMemento,
    DownloadRequest,
)
from openlist_ani.integrations.openlist import OpenlistTask, OpenlistTaskState


def _downloader():
    client = AsyncMock()
    downloader = OpenListDownloader(
        client=client,
        offline_download_tool="aria2",
        sleep=AsyncMock(),
    )
    client.mkdir = AsyncMock(return_value=True)
    client.rename_file = AsyncMock(return_value=True)
    client.move_file = AsyncMock(return_value=True)
    client.remove_path = AsyncMock(return_value=True)
    downloader._client = client
    return downloader, client


def _resource(**kwargs):
    data = {
        "title": "[ANi] 葬送的芙莉莲 - 01 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        "download_url": "magnet:?xt=urn:btih:abc123",
        "anime_name": "葬送的芙莉莲",
        "season": 1,
        "episode": 1,
        "fansub": "ANi",
        "quality": VideoQuality.Q1080P,
        "languages": [LanguageType.CHT],
    }
    data.update(kwargs)
    return AnimeRelease(**data)


async def test_download_moves_detected_file_without_renaming():
    downloader, client = _downloader()
    client.add_offline_download = AsyncMock(
        return_value=[OpenlistTask(id="offline-1", name="offline")]
    )
    client.get_offline_download_undone = AsyncMock(return_value=[])
    client.get_offline_download_done = AsyncMock(
        return_value=[
            OpenlistTask(
                id="offline-1",
                name="offline",
                state=OpenlistTaskState.SUCCEEDED,
            )
        ]
    )
    client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
    client.get_offline_download_transfer_done = AsyncMock(return_value=[])
    client.list_files = AsyncMock(
        side_effect=[
            [SimpleNamespace(name="raw episode [1080p].mkv", is_dir=False, size=100)],
            [],
        ]
    )

    result = await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(),
                base_path="/anime",
                target_directory_path="/anime/葬送的芙莉莲/Season 1",
            ),
        )
    )

    assert result.directory_path == "/anime/葬送的芙莉莲/Season 1"
    assert result.filename == "raw episode [1080p].mkv"
    client.rename_file.assert_not_awaited()
    client.add_offline_download.assert_awaited_once_with(
        urls=["magnet:?xt=urn:btih:abc123"],
        path="/anime/.oani-download-tmp/workflow-1",
        tool="aria2",
    )
    client.mkdir.assert_any_await("/anime/葬送的芙莉莲")
    client.mkdir.assert_any_await("/anime/葬送的芙莉莲/Season 1")
    client.move_file.assert_awaited_once_with(
        "/anime/.oani-download-tmp/workflow-1",
        "/anime/葬送的芙莉莲/Season 1",
        ["raw episode [1080p].mkv"],
    )
    client.remove_path.assert_awaited_once_with(
        "/anime/.oani-download-tmp", ["workflow-1"]
    )
    assert result.downloader_memento.payload["task_id"] == "offline-1"
    assert (
        result.downloader_memento.payload["temp_path"]
        == "/anime/.oani-download-tmp/workflow-1"
    )


async def test_download_creates_target_directory_incrementally_under_base_path():
    downloader, client = _downloader()
    client.add_offline_download = AsyncMock(
        return_value=[OpenlistTask(id="offline-1", name="offline")]
    )
    client.get_offline_download_undone = AsyncMock(return_value=[])
    client.get_offline_download_done = AsyncMock(
        return_value=[
            OpenlistTask(
                id="offline-1",
                name="offline",
                state=OpenlistTaskState.SUCCEEDED,
            )
        ]
    )
    client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
    client.get_offline_download_transfer_done = AsyncMock(return_value=[])
    client.list_files = AsyncMock(
        side_effect=[
            [SimpleNamespace(name="ep05.mp4", is_dir=False, size=100)],
            [],
        ]
    )

    await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(anime_name="落语朱音", episode=5),
                base_path="/PikPak/Debug",
                target_directory_path="/PikPak/Debug/落语朱音/Season 1",
            ),
        )
    )

    client.mkdir.assert_any_await("/PikPak/Debug/落语朱音")
    client.mkdir.assert_any_await("/PikPak/Debug/落语朱音/Season 1")


async def test_download_resumes_existing_openlist_task_id_without_resubmitting():
    downloader, client = _downloader()
    client.get_offline_download_undone = AsyncMock(return_value=[])
    client.get_offline_download_done = AsyncMock(
        return_value=[
            OpenlistTask(
                id="offline-existing",
                name="offline",
                state=OpenlistTaskState.SUCCEEDED,
            )
        ]
    )
    client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
    client.get_offline_download_transfer_done = AsyncMock(return_value=[])
    client.list_files = AsyncMock(
        side_effect=[
            [SimpleNamespace(name="ep01.mkv", is_dir=False, size=100)],
            [],
        ]
    )

    await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(),
                base_path="/anime",
                target_directory_path="/anime/葬送的芙莉莲/Season 1",
                downloader_memento=DownloaderMemento(
                    "openlist",
                    {
                        "task_id": "offline-existing",
                        "temp_path": "/anime/.oani-download-tmp/workflow-1",
                    },
                ),
            ),
        )
    )

    client.add_offline_download.assert_not_awaited()
    client.move_file.assert_awaited_once()
    client.remove_path.assert_awaited_once_with(
        "/anime/.oani-download-tmp", ["workflow-1"]
    )


async def test_download_advances_when_transfer_starts_before_offline_task_moves_to_done():
    downloader, client = _downloader()

    async def fail_if_waiting_before_transfer(seconds):
        if client.get_offline_download_transfer_undone.await_count == 0:
            raise AssertionError(f"workflow should not wait here: {seconds}")
        await asyncio.sleep(0)

    downloader._sleep = fail_if_waiting_before_transfer
    client.add_offline_download = AsyncMock(
        return_value=[OpenlistTask(id="offline-1", name="offline")]
    )
    client.get_offline_download_undone = AsyncMock(
        return_value=[
            OpenlistTask(
                id="offline-1",
                name="offline",
                state=OpenlistTaskState.RUNNING,
                status="uploading",
                progress=100,
            )
        ]
    )
    client.get_offline_download_done = AsyncMock(return_value=[])
    client.get_offline_download_transfer_undone = AsyncMock(
        side_effect=[
            [
                OpenlistTask(
                    id="transfer-1",
                    name="workflow-1 upload",
                    state=OpenlistTaskState.RUNNING,
                    progress=95,
                )
            ],
            [],
        ]
    )
    client.get_offline_download_transfer_done = AsyncMock(
        return_value=[
            OpenlistTask(
                id="transfer-1",
                name="workflow-1 upload",
                state=OpenlistTaskState.SUCCEEDED,
            )
        ]
    )
    client.list_files = AsyncMock(
        side_effect=[
            [SimpleNamespace(name="ep01.mkv", is_dir=False, size=100)],
            [],
        ]
    )

    result = await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(),
                base_path="/anime",
                target_directory_path="/anime/葬送的芙莉莲/Season 1",
            ),
        )
    )

    assert result.filename == "ep01.mkv"
    client.get_offline_download_transfer_undone.assert_awaited()
    client.move_file.assert_awaited_once()


async def test_download_checkpoints_submitted_state_before_waiting():
    downloader, client = _downloader()
    client.add_offline_download = AsyncMock(
        return_value=[OpenlistTask(id="offline-1", name="offline")]
    )
    client.get_offline_download_undone = AsyncMock(return_value=None)
    checkpoints = []

    async def checkpoint(memento: DownloaderMemento) -> None:
        checkpoints.append(dict(memento.payload))
        await asyncio.sleep(0)

    with pytest.raises(DownloadError):
        await downloader.download(
            PipelineContext(
                workflow_id="workflow-1",
                payload=DownloadRequest(
                    release=_resource(),
                    base_path="/anime",
                    target_directory_path="/anime/葬送的芙莉莲/Season 1",
                    checkpoint_callback=checkpoint,
                ),
            )
        )

    assert checkpoints
    submitted = checkpoints[0]
    assert submitted["workflow_state"] == "submitted"
    assert submitted["task_id"] == "offline-1"
    assert submitted["temp_path"] == "/anime/.oani-download-tmp/workflow-1"


async def test_download_resumes_from_detected_file_without_waiting_remote_tasks():
    downloader, client = _downloader()
    client.get_offline_download_undone = AsyncMock()
    client.get_offline_download_done = AsyncMock()
    client.get_offline_download_transfer_undone = AsyncMock()
    client.get_offline_download_transfer_done = AsyncMock()
    client.list_files = AsyncMock(return_value=[])
    checkpoints = []

    async def checkpoint(memento: DownloaderMemento) -> None:
        checkpoints.append(dict(memento.payload))
        await asyncio.sleep(0)

    result = await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(),
                base_path="/anime",
                target_directory_path="/anime/葬送的芙莉莲/Season 1",
                downloader_memento=DownloaderMemento(
                    "openlist",
                    {
                        "workflow_state": "file_detected",
                        "task_id": "offline-existing",
                        "temp_path": "/anime/.oani-download-tmp/workflow-1",
                        "downloaded_filename": "raw episode [1080p].mkv",
                    },
                ),
                checkpoint_callback=checkpoint,
            ),
        )
    )

    assert result.filename == "raw episode [1080p].mkv"
    client.add_offline_download.assert_not_awaited()
    client.get_offline_download_undone.assert_not_awaited()
    client.get_offline_download_done.assert_not_awaited()
    client.get_offline_download_transfer_undone.assert_not_awaited()
    client.get_offline_download_transfer_done.assert_not_awaited()
    client.move_file.assert_awaited_once_with(
        "/anime/.oani-download-tmp/workflow-1",
        "/anime/葬送的芙莉莲/Season 1",
        ["raw episode [1080p].mkv"],
    )
    assert checkpoints[-1]["workflow_state"] == "done"


async def test_download_recovers_after_conflict_rename_checkpoint_gap():
    downloader, client = _downloader()
    client.rename_file = AsyncMock(return_value=False)
    client.list_files = AsyncMock(
        side_effect=[
            [
                SimpleNamespace(
                    name="raw episode [1080p].mkv",
                    is_dir=False,
                    size=100,
                )
            ],
            [
                SimpleNamespace(
                    name="raw episode [1080p] (1).mkv",
                    is_dir=False,
                    size=100,
                )
            ],
        ]
    )

    result = await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(),
                base_path="/anime",
                target_directory_path="/anime/葬送的芙莉莲/Season 1",
                downloader_memento=DownloaderMemento(
                    "openlist",
                    {
                        "workflow_state": "file_detected",
                        "task_id": "offline-existing",
                        "temp_path": "/anime/.oani-download-tmp/workflow-1",
                        "downloaded_filename": "raw episode [1080p].mkv",
                    },
                ),
            ),
        )
    )

    assert result.filename == "raw episode [1080p] (1).mkv"
    client.move_file.assert_awaited_once_with(
        "/anime/.oani-download-tmp/workflow-1",
        "/anime/葬送的芙莉莲/Season 1",
        ["raw episode [1080p] (1).mkv"],
    )


async def test_download_resumes_materialized_file_and_cleans_temp_directory():
    downloader, client = _downloader()
    result = await downloader.download(
        PipelineContext(
            workflow_id="workflow-1",
            payload=DownloadRequest(
                release=_resource(),
                base_path="/anime",
                target_directory_path="/anime/葬送的芙莉莲/Season 1",
                downloader_memento=DownloaderMemento(
                    "openlist",
                    {
                        "workflow_state": "done",
                        "temp_path": "/anime/.oani-download-tmp/workflow-1",
                        "materialized_directory_path": "/anime/葬送的芙莉莲/Season 1",
                        "materialized_filename": "raw episode [1080p].mkv",
                    },
                ),
            ),
        )
    )

    assert result.directory_path == "/anime/葬送的芙莉莲/Season 1"
    assert result.filename == "raw episode [1080p].mkv"
    client.add_offline_download.assert_not_awaited()
    client.move_file.assert_not_awaited()
    client.remove_path.assert_awaited_once_with(
        "/anime/.oani-download-tmp", ["workflow-1"]
    )


async def test_download_raises_when_offline_task_fails():
    downloader, client = _downloader()
    client.add_offline_download = AsyncMock(
        return_value=[OpenlistTask(id="offline-1", name="offline")]
    )
    client.get_offline_download_undone = AsyncMock(return_value=[])
    client.get_offline_download_done = AsyncMock(
        return_value=[
            OpenlistTask(
                id="offline-1",
                name="offline",
                state=OpenlistTaskState.FAILED,
                error="captcha_token expired",
            )
        ]
    )

    with pytest.raises(DownloadError, match="captcha_token expired"):
        await downloader.download(
            PipelineContext(
                workflow_id="workflow-1",
                payload=DownloadRequest(
                    release=_resource(),
                    base_path="/anime",
                    target_directory_path="/anime/葬送的芙莉莲/Season 1",
                ),
            )
        )
    client.remove_path.assert_awaited_once_with(
        "/anime/.oani-download-tmp", ["workflow-1"]
    )
