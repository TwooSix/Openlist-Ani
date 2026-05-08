from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict

from openlist_ani.application.anime_library_ingestion.ports import (
    EventPublisherPort,
    TaskMementoStorePort,
)
from openlist_ani.application.common import OAniEvent, OAniEventType
from openlist_ani.domain.anime_release import AnimeRelease
from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import DownloadState, TERMINAL_STATES


class TaskCoordinator:
    """Runtime task registry backed by the durable memento store."""

    _TERMINAL_STATES = TERMINAL_STATES

    def __init__(
        self,
        task_store: TaskMementoStorePort,
        event_publisher: EventPublisherPort,
        default_base_path: str,
        terminal_history_limit: int = 100,
    ) -> None:
        self._task_store = task_store
        self._event_publisher = event_publisher
        self._default_base_path = default_base_path
        self._tasks: dict[str, TaskMemento] = {}
        self._terminal_history: OrderedDict[str, TaskMemento] = OrderedDict()
        self._terminal_history_limit = terminal_history_limit
        self._reservation_lock = asyncio.Lock()

    def load_all(self) -> list[TaskMemento]:
        tasks = self._task_store.load_all()
        self._tasks = {}
        self._terminal_history.clear()
        for task in tasks:
            if task.state in self._TERMINAL_STATES:
                self._remember_terminal(task)
            else:
                self._tasks[task.task_id] = task
        return list(tasks)

    def save(self, task_memento: TaskMemento) -> None:
        self._task_store.save(task_memento)
        if task_memento.state in self._TERMINAL_STATES:
            self._tasks.pop(task_memento.task_id, None)
            self._remember_terminal(task_memento)
            return
        self._terminal_history.pop(task_memento.task_id, None)
        self._tasks[task_memento.task_id] = task_memento

    def delete(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._task_store.delete(task_id)

    def atomic_flush(self) -> None:
        self._task_store.atomic_flush()

    async def reserve_download_task(
        self,
        release: AnimeRelease,
        base_path: str | None = None,
    ) -> TaskMemento | None:
        async with self._reservation_lock:
            if self.is_downloading(release):
                return None

            task = TaskMemento(
                task_id=str(uuid.uuid4()),
                state=DownloadState.PENDING,
                release=release,
                base_path=base_path or self._default_base_path,
            )
            self.save(task)
            await self._event_publisher.publish(
                OAniEvent(OAniEventType.TASK_CREATED, {"task_id": task.task_id})
            )
            return task

    def register_task(self, task_memento: TaskMemento) -> None:
        if task_memento.state in self._TERMINAL_STATES:
            self._remember_terminal(task_memento)
            return
        self._terminal_history.pop(task_memento.task_id, None)
        self._tasks[task_memento.task_id] = task_memento

    def is_downloading(self, release: AnimeRelease) -> bool:
        return any(
            task.release.download_url == release.download_url
            and task.state not in self._TERMINAL_STATES
            for task in self._tasks.values()
        )

    def list_tasks(self) -> list[TaskMemento]:
        return [*self._tasks.values(), *self._terminal_history.values()]

    def list_active_tasks(self) -> list[TaskMemento]:
        return [
            task
            for task in self._tasks.values()
            if task.state not in self._TERMINAL_STATES
        ]

    def get_task(self, task_id: str) -> TaskMemento | None:
        return self._tasks.get(task_id) or self._terminal_history.get(task_id)

    def _remember_terminal(self, task_memento: TaskMemento) -> None:
        if self._terminal_history_limit <= 0:
            return
        self._terminal_history[task_memento.task_id] = task_memento
        self._terminal_history.move_to_end(task_memento.task_id)
        while len(self._terminal_history) > self._terminal_history_limit:
            self._terminal_history.popitem(last=False)
