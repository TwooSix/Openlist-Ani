import asyncio

from openlist_ani.application.common import OAniEventType
from openlist_ani.application.anime_library_ingestion.task_coordinator import (
    TaskCoordinator,
)
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import DownloadState


class FakeTaskStore:
    def __init__(self, loaded=None):
        self.loaded = list(loaded or [])
        self.saved = []
        self.deleted = []
        self.flushed = False

    def load_all(self):
        return list(self.loaded)

    def save(self, task_memento):
        self.saved.append(task_memento)

    def delete(self, task_id):
        self.deleted.append(task_id)

    def atomic_flush(self):
        self.flushed = True


class FakeEventPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        await asyncio.sleep(0)
        self.events.append(event)


def _release(download_url="magnet:?xt=urn:btih:test"):
    return AnimeRelease(
        title="[ANi] Test Anime - 01 [1080p]",
        download_url=download_url,
        anime_name="Test Anime",
        season=1,
        episode=1,
    )


async def test_task_coordinator_reserves_and_persists_active_task():
    store = FakeTaskStore()
    events = FakeEventPublisher()
    coordinator = TaskCoordinator(
        task_store=store,
        event_publisher=events,
        default_base_path="/anime",
    )

    task = await coordinator.reserve_download_task(_release())
    duplicate = await coordinator.reserve_download_task(_release())

    assert task is not None
    assert duplicate is None
    assert task.state == DownloadState.PENDING
    assert task.base_path == "/anime"
    assert coordinator.get_task(task.task_id) is task
    assert coordinator.list_active_tasks() == [task]
    assert store.saved == [task]
    assert [event.event_type for event in events.events] == [OAniEventType.TASK_CREATED]


def test_task_coordinator_loads_all_tasks_but_reports_only_active_tasks():
    pending = TaskMemento(
        task_id="pending",
        state=DownloadState.PENDING,
        release=_release("magnet:?xt=urn:btih:pending"),
        base_path="/anime",
    )
    failed = TaskMemento(
        task_id="failed",
        state=DownloadState.FAILED,
        release=_release("magnet:?xt=urn:btih:failed"),
        base_path="/anime",
    )
    completed = TaskMemento(
        task_id="completed",
        state=DownloadState.COMPLETED,
        release=_release("magnet:?xt=urn:btih:completed"),
        base_path="/anime",
    )
    coordinator = TaskCoordinator(
        task_store=FakeTaskStore([pending, failed, completed]),
        event_publisher=FakeEventPublisher(),
        default_base_path="/anime",
    )

    loaded = coordinator.load_all()

    assert loaded == [pending, failed, completed]
    assert coordinator.list_tasks() == [pending, failed, completed]
    assert coordinator.list_active_tasks() == [pending]


def test_task_coordinator_removes_deleted_task_from_memory():
    task = TaskMemento(
        task_id="pending",
        state=DownloadState.PENDING,
        release=_release("magnet:?xt=urn:btih:pending"),
        base_path="/anime",
    )
    store = FakeTaskStore()
    coordinator = TaskCoordinator(
        task_store=store,
        event_publisher=FakeEventPublisher(),
        default_base_path="/anime",
    )
    coordinator.register_task(task)

    coordinator.delete(task.task_id)

    assert coordinator.get_task(task.task_id) is None
    assert coordinator.list_tasks() == []
    assert store.deleted == [task.task_id]


def test_task_coordinator_keeps_bounded_terminal_history_on_save():
    first = TaskMemento(
        task_id="failed-1",
        state=DownloadState.PENDING,
        release=_release("magnet:?xt=urn:btih:failed-1"),
        base_path="/anime",
    )
    second = TaskMemento(
        task_id="failed-2",
        state=DownloadState.PENDING,
        release=_release("magnet:?xt=urn:btih:failed-2"),
        base_path="/anime",
    )
    store = FakeTaskStore()
    coordinator = TaskCoordinator(
        task_store=store,
        event_publisher=FakeEventPublisher(),
        default_base_path="/anime",
        terminal_history_limit=1,
    )
    coordinator.register_task(first)
    coordinator.register_task(second)

    first.state = DownloadState.FAILED
    coordinator.save(first)
    second.state = DownloadState.FAILED
    coordinator.save(second)

    assert coordinator.get_task(first.task_id) is None
    assert coordinator.get_task(second.task_id) is second
    assert coordinator.list_active_tasks() == []
    assert coordinator.list_tasks() == [second]
    assert store.saved == [first, second]
