import asyncio
from dataclasses import dataclass, field

import pytest

from openlist_ani.domain.download_task.downloader import (
    DownloadError,
    DownloadedFile,
    DownloaderMemento,
    DownloadRequest,
)
from openlist_ani.domain.download_task.file_renamer import (
    RenamedFile,
    RenameRequest,
)
from openlist_ani.domain.download_task.task import DownloadState
from openlist_ani.adapters.outbound.events import OAniEventManager
from openlist_ani.application.anime_library_ingestion import (
    AnimeLibraryIngestionPipeline,
)
from openlist_ani.application.anime_library_ingestion.buffer import PipelineBuffer
from openlist_ani.application.anime_library_ingestion.settings import (
    AnimeLibraryIngestionSettings,
    PrioritySettings,
)
from openlist_ani.application.anime_library_ingestion.models import (
    DownloadCandidate,
    ParseResult,
    PipelineContext,
    ReleaseTitleParseResult,
)
from openlist_ani.application.anime_library_ingestion.filters import (
    FilterChain,
    PriorityFilter,
    StrictRenameFilter,
)
from openlist_ani.application.anime_library_ingestion.stages import RSSStage
from openlist_ani.adapters.outbound.persistence import (
    JsonTaskMementoStore,
    SqliteAnimeLibraryRepository,
)
from openlist_ani.domain.anime_release import AnimeRelease, LanguageType, VideoQuality
from openlist_ani.domain.download_task.memento import TaskMemento


@dataclass
class FakeDownloader:
    downloaded: list[str]

    @property
    def downloader_type(self) -> str:
        return "fake"

    async def download(
        self, context: PipelineContext[DownloadRequest]
    ) -> DownloadedFile:
        await asyncio.sleep(0)
        request = context.payload
        self.downloaded.append(request.release.title)
        return DownloadedFile(
            release=request.release,
            directory_path=request.target_directory_path,
            filename="raw episode [1080p].mkv",
            downloader_memento=DownloaderMemento("fake", {"seen": True}),
        )


@dataclass
class FakeFileRenamer:
    renamed: list[str]

    async def rename(self, context: PipelineContext[RenameRequest]) -> RenamedFile:
        await asyncio.sleep(0)
        request = context.payload
        self.renamed.append(request.target_filename)
        return RenamedFile(
            release=request.release,
            directory_path=request.directory_path,
            filename=request.target_filename,
        )


class FakeNotification:
    def __init__(self):
        self.sent = []

    async def send_download_complete_notification(self, anime_name, title):
        await asyncio.sleep(0)
        self.sent.append((anime_name, title))
        return {"fake": True}


class FailingNotification:
    async def send_download_complete_notification(self, anime_name, title):
        await asyncio.sleep(0)
        return {"fake": False}


class FakeMetadataParser:
    async def parse(self, entries):
        await asyncio.sleep(0)
        raise AssertionError("metadata parser should not be called in these tests")


class PassingMetadataParser:
    async def parse(self, entries):
        await asyncio.sleep(0)
        return [
            ParseResult(
                success=True,
                result=ReleaseTitleParseResult(
                    anime_name="Test Anime",
                    season=1,
                    episode=index + 1,
                    quality=VideoQuality.Q1080P,
                    fansub="ANi",
                    languages=[],
                    version=1,
                ),
            )
            for index, _entry in enumerate(entries)
        ]


class CountingMetadataParser(PassingMetadataParser):
    def __init__(self):
        self.calls = 0

    async def parse(self, entries):
        self.calls += 1
        return await super().parse(entries)


class FakeFeedReader:
    def __init__(self, entries):
        self.entries = entries

    async def fetch_new_releases(self):
        await asyncio.sleep(0)
        return self.entries


class FakeActiveTaskQuery:
    def __init__(self, tasks):
        self._tasks = list(tasks)

    def list_active_tasks(self):
        return [
            task
            for task in self._tasks
            if task.state
            not in {
                DownloadState.COMPLETED,
                DownloadState.FAILED,
                DownloadState.CANCELLED,
            }
        ]


class RecordingTaskReservation:
    def __init__(self):
        self.tasks = []

    async def reserve_download_task(self, release, base_path=None):
        await asyncio.sleep(0)
        task = TaskMemento(
            task_id=f"reserved-{len(self.tasks) + 1}",
            state=DownloadState.PENDING,
            release=release,
            base_path=base_path or "/anime",
        )
        self.tasks.append(task)
        return task


async def _drain_download_candidates(
    buffer: PipelineBuffer[PipelineContext[DownloadCandidate]],
) -> list[DownloadCandidate]:
    drained: list[DownloadCandidate] = []
    while not buffer.empty():
        item = await buffer.get()
        drained.append(item.payload)
        buffer.task_done()
    return drained


@dataclass
class SlowDownloader(FakeDownloader):
    active: int = 0
    max_active: int = 0

    async def download(
        self, context: PipelineContext[DownloadRequest]
    ) -> DownloadedFile:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return await super().download(context)


@dataclass
class FailingThenSuccessfulDownloader(FakeDownloader):
    failures_before_success: int
    attempts: int = 0

    async def download(
        self, context: PipelineContext[DownloadRequest]
    ) -> DownloadedFile:
        self.attempts += 1
        if self.attempts <= self.failures_before_success:
            raise DownloadError(
                "OpenList offline download failed: captcha_token expired"
            )
        return await super().download(context)


@dataclass
class CheckpointThenSuccessfulDownloader(FakeDownloader):
    attempts: int = 0
    seen_mementos: list[dict] = field(default_factory=list)

    async def download(
        self, context: PipelineContext[DownloadRequest]
    ) -> DownloadedFile:
        request = context.payload
        self.attempts += 1
        self.seen_mementos.append(
            dict(request.downloader_memento.payload)
            if request.downloader_memento
            else {}
        )
        if self.attempts == 1:
            await request.checkpoint_callback(
                DownloaderMemento(
                    "fake",
                    {"workflow_state": "submitted", "task_id": "remote-1"},
                )
            )
            raise DownloadError("transient")
        return await super().download(context)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    return SqliteAnimeLibraryRepository(tmp_path / "data.db")


def _runtime(
    tmp_path,
    downloader,
    file_renamer,
    store,
    event_manager,
    repository,
    notification,
    download_concurrency=3,
):
    return AnimeLibraryIngestionPipeline(
        downloader=downloader,
        file_renamer=file_renamer,
        task_store=store,
        event_publisher=event_manager,
        anime_library_repository=repository,
        metadata_parser=FakeMetadataParser(),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=300,
            download_concurrency=download_concurrency,
        ),
        notifier=notification,
    )


async def test_rss_stage_enqueues_unique_download_candidates(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    download_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]] = (
        PipelineBuffer("download")
    )
    entries = [
        AnimeRelease(title="Test Anime - 01", download_url="magnet:?xt=1"),
        AnimeRelease(title="Test Anime - 01 duplicate", download_url="magnet:?xt=1"),
        AnimeRelease(title="Test Anime - 02", download_url="magnet:?xt=2"),
        AnimeRelease(title="No URL", download_url=""),
    ]
    stage = RSSStage(
        feed_reader=FakeFeedReader(entries),
        metadata_parser=PassingMetadataParser(),
        anime_library_repository=isolated_db,
        output_buffer=download_buffer,
        event_publisher=event_manager,
        filter_chain=FilterChain([]),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=0,
        ),
        interval_seconds=0,
        task_reservation=RecordingTaskReservation(),
    )

    await stage.process_batch()

    candidates = await _drain_download_candidates(download_buffer)
    assert [candidate.release.title for candidate in candidates] == [
        "Test Anime - 01",
        "Test Anime - 02",
    ]
    assert [candidate.base_path for candidate in candidates] == [
        "/anime",
        "/anime",
    ]
    await event_manager.stop()


async def test_rss_stage_blocks_strict_conflict_with_active_task(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    active_release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:active-a",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    active_task = TaskMemento(
        task_id="active-a",
        state=DownloadState.DOWNLOADING,
        release=active_release,
        base_path="/anime",
    )
    active_tasks = FakeActiveTaskQuery([active_task])
    download_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]] = (
        PipelineBuffer("download")
    )
    stage = RSSStage(
        feed_reader=FakeFeedReader(
            [
                AnimeRelease(
                    title="[ANi] Test Anime alternative - 01 [1080p]",
                    download_url="magnet:?xt=urn:btih:active-b",
                )
            ]
        ),
        metadata_parser=PassingMetadataParser(),
        anime_library_repository=isolated_db,
        output_buffer=download_buffer,
        event_publisher=event_manager,
        filter_chain=FilterChain(
            [
                StrictRenameFilter(
                    "{anime_name} S{season:02d}E{episode:02d}",
                    isolated_db,
                    active_task_query=active_tasks,
                    base_path="/anime",
                )
            ]
        ),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=0,
            strict_filtering=True,
        ),
        interval_seconds=0,
        task_reservation=RecordingTaskReservation(),
    )

    await stage.process_batch()

    assert download_buffer.empty()
    await event_manager.stop()


async def test_rss_stage_reserves_task_before_download_buffer_is_consumed(
    tmp_path, isolated_db
):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    runtime = _runtime(
        tmp_path,
        FakeDownloader([]),
        FakeFileRenamer([]),
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        FakeNotification(),
    )
    feed_reader = FakeFeedReader(
        [
            AnimeRelease(
                title="[ANi] Test Anime - 01 [1080p]",
                download_url="magnet:?xt=urn:btih:reserved-a",
            )
        ]
    )
    download_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]] = (
        PipelineBuffer("download")
    )
    stage = RSSStage(
        feed_reader=feed_reader,
        metadata_parser=PassingMetadataParser(),
        anime_library_repository=isolated_db,
        output_buffer=download_buffer,
        event_publisher=event_manager,
        filter_chain=FilterChain(
            [
                StrictRenameFilter(
                    "{anime_name} S{season:02d}E{episode:02d}",
                    isolated_db,
                    active_task_query=runtime.task_coordinator,
                    base_path="/anime",
                )
            ]
        ),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=0,
            strict_filtering=True,
        ),
        interval_seconds=0,
        task_reservation=runtime.task_coordinator,
    )

    await stage.process_batch()
    feed_reader.entries = [
        AnimeRelease(
            title="[ANi] Test Anime alternative - 01 [1080p]",
            download_url="magnet:?xt=urn:btih:reserved-b",
        )
    ]
    await stage.process_batch()

    candidates = await _drain_download_candidates(download_buffer)
    assert [candidate.release.download_url for candidate in candidates] == [
        "magnet:?xt=urn:btih:reserved-a"
    ]
    assert [
        task.release.download_url
        for task in runtime.task_coordinator.list_active_tasks()
    ] == ["magnet:?xt=urn:btih:reserved-a"]
    await event_manager.stop()


async def test_rss_stage_blocks_lower_priority_release_with_active_task(
    tmp_path, isolated_db
):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    active_task = TaskMemento(
        task_id="priority-active",
        state=DownloadState.DOWNLOADING,
        release=AnimeRelease(
            title="[GoodSub] Test Anime - 01 [1080p][简]",
            download_url="magnet:?xt=urn:btih:priority-active",
            anime_name="Test Anime",
            season=1,
            episode=1,
            fansub="GoodSub",
            quality=VideoQuality.Q1080P,
            languages=[LanguageType.CHS],
        ),
        base_path="/anime",
    )
    download_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]] = (
        PipelineBuffer("download")
    )
    stage = RSSStage(
        feed_reader=FakeFeedReader(
            [
                AnimeRelease(
                    title="[BadSub] Test Anime - 01 [1080p][简]",
                    download_url="magnet:?xt=urn:btih:priority-lower",
                )
            ]
        ),
        metadata_parser=PassingMetadataParser(),
        anime_library_repository=isolated_db,
        output_buffer=download_buffer,
        event_publisher=event_manager,
        filter_chain=FilterChain(
            [
                PriorityFilter(
                    PrioritySettings(
                        fansub=["GoodSub", "ANi"],
                        quality=[],
                        field_order=["fansub"],
                    ),
                    isolated_db,
                    active_task_query=FakeActiveTaskQuery([active_task]),
                )
            ]
        ),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=0,
        ),
        interval_seconds=0,
        task_reservation=RecordingTaskReservation(),
    )

    await stage.process_batch()

    assert download_buffer.empty()
    await event_manager.stop()


async def test_rss_stage_reuses_metadata_cache_for_rejected_candidates(
    tmp_path, isolated_db
):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    parser = CountingMetadataParser()
    active_task = TaskMemento(
        task_id="active-a",
        state=DownloadState.DOWNLOADING,
        release=AnimeRelease(
            title="[ANi] Test Anime - 01 [1080p]",
            download_url="magnet:?xt=urn:btih:active-a",
            anime_name="Test Anime",
            season=1,
            episode=1,
            quality=VideoQuality.Q1080P,
        ),
        base_path="/anime",
    )
    download_buffer: PipelineBuffer[PipelineContext[DownloadCandidate]] = (
        PipelineBuffer("download")
    )
    stage = RSSStage(
        feed_reader=FakeFeedReader(
            [
                AnimeRelease(
                    title="[ANi] Test Anime alternative - 01 [1080p]",
                    download_url="magnet:?xt=urn:btih:active-b",
                )
            ]
        ),
        metadata_parser=parser,
        anime_library_repository=isolated_db,
        output_buffer=download_buffer,
        event_publisher=event_manager,
        filter_chain=FilterChain(
            [
                StrictRenameFilter(
                    "{anime_name} S{season:02d}E{episode:02d}",
                    isolated_db,
                    active_task_query=FakeActiveTaskQuery([active_task]),
                    base_path="/anime",
                )
            ]
        ),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=0,
            strict_filtering=True,
        ),
        interval_seconds=0,
        task_reservation=RecordingTaskReservation(),
    )

    await stage.process_batch()
    await stage.process_batch()

    assert parser.calls == 1
    assert download_buffer.empty()
    await event_manager.stop()


async def test_manual_download_runs_download_rename_notify_chain(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = FakeDownloader([])
    file_renamer = FakeFileRenamer([])
    notification = FakeNotification()
    runtime = _runtime(
        tmp_path,
        downloader,
        file_renamer,
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        notification,
    )
    await runtime.start()

    release = AnimeRelease(
        title="[桜都字幕组] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:test",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    task = await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()

    assert task is not None
    assert downloader.downloaded == [release.title]
    assert file_renamer.renamed == ["Test Anime S01E01.mkv"]
    assert notification.sent == [("Test Anime", release.title)]
    assert (
        runtime.task_coordinator.get_task(task.task_id).state == DownloadState.COMPLETED
    )
    assert await isolated_db.is_downloaded(release.title)

    await runtime.stop()
    await event_manager.stop()


async def test_active_task_query_excludes_failed_tasks(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    runtime = AnimeLibraryIngestionPipeline(
        downloader=FakeDownloader([]),
        file_renamer=FakeFileRenamer([]),
        task_store=JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_publisher=event_manager,
        anime_library_repository=isolated_db,
        metadata_parser=FakeMetadataParser(),
        settings=AnimeLibraryIngestionSettings(
            download_path="/anime",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
            rss_interval_seconds=300,
            strict_filtering=True,
        ),
        notifier=FakeNotification(),
    )
    failed_release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:failed-task",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    runtime.task_coordinator.register_task(
        TaskMemento(
            task_id="failed-task",
            state=DownloadState.FAILED,
            release=failed_release,
            base_path="/anime",
        )
    )
    candidate = AnimeRelease(
        title="[ANi] Test Anime alternative - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:manual-b",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )

    result = await StrictRenameFilter(
        "{anime_name} S{season:02d}E{episode:02d}",
        isolated_db,
        active_task_query=runtime.task_coordinator,
        base_path="/anime",
    ).apply([candidate])

    assert result == [candidate]
    assert runtime.task_coordinator.list_active_tasks() == []
    await event_manager.stop()


async def test_manual_download_rejects_same_active_download_url(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    runtime = _runtime(
        tmp_path,
        FakeDownloader([]),
        FakeFileRenamer([]),
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        FakeNotification(),
    )
    active_release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:manual-url",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    runtime.task_coordinator.register_task(
        TaskMemento(
            task_id="manual-active",
            state=DownloadState.DOWNLOADING,
            release=active_release,
            base_path="/anime",
        )
    )

    task = await runtime.submit_download(
        active_release,
        "/anime",
    )

    assert task is None
    await event_manager.stop()


async def test_missing_notifier_does_not_block_completion(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    runtime = _runtime(
        tmp_path,
        FakeDownloader([]),
        FakeFileRenamer([]),
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        None,
        download_concurrency=1,
    )
    await runtime.start()

    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:notification-disabled",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    task = await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()
    await runtime.stop()
    await event_manager.stop()

    assert (
        runtime.task_coordinator.get_task(task.task_id).state == DownloadState.COMPLETED
    )
    assert await isolated_db.is_downloaded(release.title)


async def test_notification_failure_does_not_block_completion(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    runtime = _runtime(
        tmp_path,
        FakeDownloader([]),
        FakeFileRenamer([]),
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        FailingNotification(),
        download_concurrency=1,
    )
    await runtime.start()

    release = AnimeRelease(
        title="[ANi] Test Anime - 03 [1080p]",
        download_url="magnet:?xt=urn:btih:notification-failed",
        anime_name="Test Anime",
        season=1,
        episode=3,
        quality=VideoQuality.Q1080P,
    )
    task = await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()
    await runtime.stop()
    await event_manager.stop()

    assert (
        runtime.task_coordinator.get_task(task.task_id).state == DownloadState.COMPLETED
    )
    assert await isolated_db.is_downloaded(release.title)


async def test_runtime_marks_restore_failure_for_missing_downloaded_file(
    tmp_path, isolated_db
):
    await isolated_db.init()
    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:restore-missing-file",
        anime_name="Test Anime",
        season=1,
        episode=1,
    )
    store = JsonTaskMementoStore(tmp_path / "task_mementos.json")
    task = TaskMemento(
        task_id="restore-missing-file",
        state=DownloadState.DOWNLOADED,
        release=release,
        base_path="/anime",
        downloader=DownloaderMemento("fake", {"task_id": "offline-1"}),
    )
    task.pipeline.next_buffer = "rename"
    store.save(task)

    event_manager = OAniEventManager()
    await event_manager.start()
    runtime = _runtime(
        tmp_path,
        FakeDownloader([]),
        FakeFileRenamer([]),
        store,
        event_manager,
        isolated_db,
        FakeNotification(),
    )

    await runtime.start()
    await runtime.stop()
    await event_manager.stop()

    saved = runtime.task_coordinator.get_task("restore-missing-file")
    assert saved.state == DownloadState.FAILED
    assert "missing downloaded file" in saved.retry.last_error


async def test_download_stage_processes_multiple_releases_concurrently(
    tmp_path, isolated_db
):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = SlowDownloader([])
    file_renamer = FakeFileRenamer([])
    notification = FakeNotification()
    runtime = _runtime(
        tmp_path,
        downloader,
        file_renamer,
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        notification,
        download_concurrency=2,
    )
    await runtime.start()

    releases = [
        AnimeRelease(
            title=f"[ANi] Test Anime - 0{episode} [1080p]",
            download_url=f"magnet:?xt=urn:btih:concurrent-{episode}",
            anime_name="Test Anime",
            season=1,
            episode=episode,
            quality=VideoQuality.Q1080P,
        )
        for episode in (1, 2)
    ]
    for release in releases:
        await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()

    assert downloader.max_active > 1

    await runtime.stop()
    await event_manager.stop()


async def test_download_stage_retries_transient_download_failure(tmp_path, isolated_db):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = FailingThenSuccessfulDownloader([], failures_before_success=3)
    file_renamer = FakeFileRenamer([])
    notification = FakeNotification()
    runtime = _runtime(
        tmp_path,
        downloader,
        file_renamer,
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        notification,
        download_concurrency=1,
    )
    await runtime.start()

    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:retry-success",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    task = await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()

    saved = runtime.task_coordinator.get_task(task.task_id)
    assert downloader.attempts > 1
    assert saved.state == DownloadState.COMPLETED
    assert saved.retry.retry_count > 0
    assert file_renamer.renamed == ["Test Anime S01E01.mkv"]

    await runtime.stop()
    await event_manager.stop()


async def test_download_stage_persists_downloader_checkpoint_before_retry(
    tmp_path, isolated_db
):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = CheckpointThenSuccessfulDownloader([])
    runtime = _runtime(
        tmp_path,
        downloader,
        FakeFileRenamer([]),
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        FakeNotification(),
        download_concurrency=1,
    )
    await runtime.start()

    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:checkpoint-retry",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()

    assert downloader.attempts > 1
    assert {"workflow_state": "submitted", "task_id": "remote-1"} in (
        downloader.seen_mementos
    )

    await runtime.stop()
    await event_manager.stop()


async def test_download_stage_marks_failed_after_retry_exhaustion_without_reraising(
    tmp_path, isolated_db, monkeypatch
):
    await isolated_db.init()
    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = FailingThenSuccessfulDownloader([], failures_before_success=4)
    file_renamer = FakeFileRenamer([])
    notification = FakeNotification()
    runtime = _runtime(
        tmp_path,
        downloader,
        file_renamer,
        JsonTaskMementoStore(tmp_path / "task_mementos.json"),
        event_manager,
        isolated_db,
        notification,
        download_concurrency=1,
    )
    await runtime.start()

    unexpected_stage_errors = []

    async def fail_on_stage_error(error, item=None):
        await asyncio.sleep(0)
        unexpected_stage_errors.append(str(error))

    monkeypatch.setattr(runtime._stages[0], "on_error", fail_on_stage_error)

    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:retry-failed",
        anime_name="Test Anime",
        season=1,
        episode=1,
        quality=VideoQuality.Q1080P,
    )
    task = await runtime.submit_download(release, "/anime")

    await runtime.download_buffer.join()

    saved = runtime.task_coordinator.get_task(task.task_id)
    assert downloader.attempts > 1
    assert saved.state == DownloadState.FAILED
    assert saved.retry.retry_count >= saved.retry.max_retries
    assert "captcha_token" in saved.retry.last_error
    assert file_renamer.renamed == []
    assert unexpected_stage_errors == []

    await runtime.stop()
    await event_manager.stop()


async def test_runtime_restores_downloaded_task_to_rename(tmp_path, isolated_db):
    await isolated_db.init()
    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:restore",
        anime_name="Test Anime",
        season=1,
        episode=1,
    )
    store = JsonTaskMementoStore(tmp_path / "task_mementos.json")
    task = TaskMemento(
        task_id="restore-1",
        state=DownloadState.DOWNLOADED,
        release=release,
        base_path="/anime",
        downloader=DownloaderMemento("fake", {"task_id": "offline-1"}),
    )
    task.pipeline.next_buffer = "rename"
    task.pipeline.downloaded_directory_path = "/anime/Test Anime/Season 1"
    task.pipeline.downloaded_filename = "raw episode [1080p].mkv"
    store.save(task)

    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = FakeDownloader([])
    file_renamer = FakeFileRenamer([])
    notification = FakeNotification()
    runtime = _runtime(
        tmp_path,
        downloader,
        file_renamer,
        store,
        event_manager,
        isolated_db,
        notification,
    )

    await runtime.start()
    await runtime.rename_buffer.join()
    await runtime.notification_buffer.join()

    assert downloader.downloaded == []
    assert file_renamer.renamed == ["Test Anime S01E01.mkv"]
    assert notification.sent == [("Test Anime", release.title)]
    assert (
        runtime.task_coordinator.get_task("restore-1").state == DownloadState.COMPLETED
    )

    await runtime.stop()
    await event_manager.stop()


async def test_runtime_does_not_restore_retry_exhausted_download_task(
    tmp_path, isolated_db
):
    await isolated_db.init()
    release = AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url="magnet:?xt=urn:btih:restore-exhausted",
        anime_name="Test Anime",
        season=1,
        episode=1,
    )
    store = JsonTaskMementoStore(tmp_path / "task_mementos.json")
    task = TaskMemento(
        task_id="restore-exhausted",
        state=DownloadState.DOWNLOADING,
        release=release,
        base_path="/anime",
    )
    task.retry.retry_count = task.retry.max_retries
    task.retry.last_error = "captcha_token expired"
    store.save(task)

    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = FakeDownloader([])
    runtime = _runtime(
        tmp_path,
        downloader,
        FakeFileRenamer([]),
        store,
        event_manager,
        isolated_db,
        FakeNotification(),
    )

    await runtime.start()
    await runtime.download_buffer.join()

    assert downloader.downloaded == []
    assert (
        runtime.task_coordinator.get_task("restore-exhausted").state
        == DownloadState.FAILED
    )

    await runtime.stop()
    await event_manager.stop()


async def test_runtime_restores_renamed_task_to_notification(tmp_path, isolated_db):
    await isolated_db.init()
    release = AnimeRelease(
        title="[ANi] Test Anime - 02 [1080p]",
        download_url="magnet:?xt=urn:btih:notify",
        anime_name="Test Anime",
        season=1,
        episode=2,
    )
    store = JsonTaskMementoStore(tmp_path / "task_mementos.json")
    task = TaskMemento(
        task_id="restore-2",
        state=DownloadState.RENAMED,
        release=release,
        base_path="/anime",
    )
    task.pipeline.next_buffer = "notification"
    task.pipeline.renamed_path = "/anime/Test Anime/Season 1/Test Anime S01E02.mkv"
    store.save(task)

    event_manager = OAniEventManager()
    await event_manager.start()
    downloader = FakeDownloader([])
    file_renamer = FakeFileRenamer([])
    notification = FakeNotification()
    runtime = _runtime(
        tmp_path,
        downloader,
        file_renamer,
        store,
        event_manager,
        isolated_db,
        notification,
    )

    await runtime.start()
    await runtime.notification_buffer.join()

    assert downloader.downloaded == []
    assert file_renamer.renamed == []
    assert notification.sent == [("Test Anime", release.title)]
    assert (
        runtime.task_coordinator.get_task("restore-2").state == DownloadState.COMPLETED
    )

    await runtime.stop()
    await event_manager.stop()
