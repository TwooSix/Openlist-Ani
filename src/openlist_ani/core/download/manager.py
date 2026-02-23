"""
Download manager module.

This module provides the DownloadManager class which orchestrates download events,
manages state persistence, and coordinates with a single downloader implementation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from openlist_ani.logger import logger

from ..website.model import AnimeResourceInfo
from .downloader.base import HandlerResult, HandlerStatus
from .model.task import DownloadState, DownloadTask

if TYPE_CHECKING:
    from .downloader.base import BaseDownloader


class DownloadManager:

    _NEXT_STATE: dict[DownloadState, DownloadState] = {
        DownloadState.PENDING: DownloadState.DOWNLOADING,
        DownloadState.DOWNLOADING: DownloadState.TRANSFERRING,
        DownloadState.TRANSFERRING: DownloadState.CLEANING_UP,
        DownloadState.CLEANING_UP: DownloadState.COMPLETED,
    }

    _TERMINAL_STATES = frozenset(
        {
            DownloadState.COMPLETED,
            DownloadState.FAILED,
            DownloadState.CANCELLED,
        }
    )

    def __init__(
        self,
        downloader: BaseDownloader,
        state_file: str = "data/pending_downloads.json",
        poll_interval: float = 60.0,
        max_concurrent: int = 3,
    ):
        self._downloader = downloader
        self.state_file = Path(state_file)
        self.poll_interval = poll_interval
        self._events: dict[str, DownloadTask] = {}
        self._events_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._background_tasks: set[asyncio.Task[None]] = set()

        self._handlers: dict[DownloadState, Callable] = {
            DownloadState.PENDING: downloader.on_pending,
            DownloadState.DOWNLOADING: downloader.on_downloading,
            DownloadState.TRANSFERRING: downloader.on_transferring,
            DownloadState.CLEANING_UP: downloader.on_cleaning_up,
        }

        self._on_state_change: list[Callable[[DownloadTask, DownloadState], None]] = []
        self._on_complete: list[Callable[[DownloadTask], None]] = []
        self._on_error: list[Callable[[DownloadTask, str], None]] = []

        self._load_state()
        logger.info(f"Initialized with {type(downloader).__name__}")

        self._schedule_recovered_tasks_if_possible()

    def _schedule_recovered_tasks_if_possible(self) -> None:
        """Schedule recovered tasks only when running inside an active event loop."""
        if not self._events:
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(
                "Skip auto-resume scheduling: no running event loop during DownloadManager initialization"
            )
            return

        # Auto-start pending tasks (recovered from state file)
        for event in self._events.values():
            # Only auto-start non-terminal states (PENDING, DOWNLOADING, etc.)
            if event.state not in (
                DownloadState.COMPLETED,
                DownloadState.FAILED,
                DownloadState.CANCELLED,
            ):
                background_task = asyncio.create_task(self._process_task(event))
                self._background_tasks.add(background_task)
                background_task.add_done_callback(self._background_tasks.discard)

    @property
    def downloader(self) -> BaseDownloader:
        return self._downloader

    def _load_state(self) -> None:
        """Load persisted tasks from state file."""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for event_id, event_data in data.items():
                event = DownloadTask.from_dict(event_data)
                # Only load non-terminal tasks
                if event.state not in (
                    DownloadState.COMPLETED,
                    DownloadState.FAILED,
                    DownloadState.CANCELLED,
                ):
                    self._events[event_id] = event

            if self._events:
                logger.info(f"Resuming {len(self._events)} pending download(s)")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            self._events = {}

    def _save_state(self) -> None:
        """Persist active tasks to state file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            # Only save non-terminal tasks
            data = {
                event_id: event.to_dict()
                for event_id, event in self._events.items()
                if event.state
                not in (
                    DownloadState.COMPLETED,
                    DownloadState.FAILED,
                    DownloadState.CANCELLED,
                )
            }

            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def get_event(self, event_id: str) -> DownloadTask | None:
        """Get an event by ID."""
        return self._events.get(event_id)

    def on_complete(self, callback: Callable[[DownloadTask], None]) -> None:
        """Register a callback to be called when a download completes successfully.

        Args:
            callback: Function to call with the completed task.
                     Can be sync or async function.

        Example:
            async def save_to_db(task):
                await db.add_resource(task.resource_info)

            manager.on_complete(save_to_db)
        """
        self._on_complete.append(callback)

    def on_error(self, callback: Callable[[DownloadTask, str], None]) -> None:
        """Register a callback to be called when a download fails.

        Args:
            callback: Function to call with the failed task and error message.
        """
        self._on_error.append(callback)

    def is_downloading(self, resource_info: AnimeResourceInfo) -> bool:
        """Check if a resource is currently downloading.

        Args:
            resource_info: The resource to check

        Returns:
            True if the resource is currently downloading
        """
        for task in self._events.values():
            if task.resource_info.download_url == resource_info.download_url:
                return True
        return False

    async def _process_task(self, task: DownloadTask) -> None:
        async with self._semaphore:
            await self._run_state_machine(task)

    def _emit_state_change(self, task: DownloadTask, new_state: DownloadState) -> None:
        """Trigger state change callbacks."""
        for callback in self._on_state_change:
            try:
                callback(task, new_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")

    async def _finalize_task(self, task: DownloadTask, success: bool) -> None:
        """Finalize a task (success or final failure) and remove it from events.

        Args:
            task: The task to finalize
            success: True if completed successfully, False if failed
        """
        self._save_state()

        await self._run_finalize_callbacks(task, success)
        await self._remove_task_from_events(task, success)

    async def _run_finalize_callbacks(self, task: DownloadTask, success: bool) -> None:
        """Execute finalization callbacks based on success state."""
        callbacks = self._on_complete if success else self._on_error
        error_message = task.error_message or "Unknown error"

        for callback in callbacks:
            try:
                result = callback(task) if success else callback(task, error_message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _remove_task_from_events(self, task: DownloadTask, success: bool) -> None:
        """Remove finalized task from in-memory events map."""
        async with self._events_lock:
            if task.id in self._events:
                del self._events[task.id]
                logger.debug(
                    f"Task finalized and removed: {task.id} (success={success})"
                )

    async def _run_state_machine(self, task: DownloadTask) -> None:
        while task.state not in self._TERMINAL_STATES:
            if task.state == DownloadState.PENDING:
                logger.info(f"Starting download: {task.resource_info.title}")

            handler = self._handlers.get(task.state)
            if not handler:
                task.mark_failed(f"No handler for state: {task.state}")
                break

            try:
                result: HandlerResult = await handler(task)
            except asyncio.CancelledError:
                self._save_state()
                raise
            except Exception as e:
                logger.exception(f"Handler error [{task.state}]: {e}")
                result = HandlerResult.fail(str(e))

            match result.status:
                case HandlerStatus.DONE:
                    next_state = self._NEXT_STATE[task.state]
                    task.update_state(next_state)
                    self._save_state()
                    self._emit_state_change(task, next_state)

                case HandlerStatus.POLL:
                    await asyncio.sleep(result.poll_delay)

                case HandlerStatus.FAILED:
                    task.mark_failed(result.error_message or "Handler failed")

        await self._handle_terminal_state(task)

    async def _handle_terminal_state(self, task: DownloadTask) -> None:
        match task.state:
            case DownloadState.COMPLETED:
                logger.info(f"Download completed: {task.final_path}")
                await self._finalize_task(task, success=True)

            case DownloadState.FAILED:
                if task.can_retry():
                    logger.warning(
                        f"Task failed (attempt {task.retry_count}/{task.max_retries}), "
                        f"retrying: {task.resource_info.title}"
                    )
                    await self._downloader.on_failed(task)
                    task.retry()
                    self._save_state()
                    await self._run_state_machine(task)
                else:
                    logger.error(
                        f"Task failed after {task.retry_count} retries: "
                        f"{task.resource_info.title}"
                    )
                    await self._downloader.on_failed(task)
                    await self._finalize_task(task, success=False)

            case DownloadState.CANCELLED:
                await self._downloader.on_cancelled(task)
                await self._finalize_task(task, success=False)

    async def download(self, resource_info: AnimeResourceInfo, save_path: str) -> bool:
        """Download anime resource."""
        task = DownloadTask.from_resource_info(resource_info, save_path)

        async with self._events_lock:
            self._events[task.id] = task
        self._save_state()

        await self._process_task(task)
        return task.state == DownloadState.COMPLETED
