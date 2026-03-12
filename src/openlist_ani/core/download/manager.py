"""
Download manager module.

This module provides the DownloadManager class which orchestrates download tasks,
manages state persistence, and coordinates with a single downloader implementation.

Public API:
    - download(resource_info, save_path) -> bool: Blocking download.
    - submit(resource_info, save_path) -> DownloadTask: Non-blocking background download.
    - is_downloading(resource_info) -> bool: Check if a resource is being downloaded.
    - list_tasks() -> list[DownloadTask]: List all active tasks.
    - get_task(task_id) -> DownloadTask | None: Get a specific task.
    - on_complete(callback): Register completion callback.
    - on_error(callback): Register error callback.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ...logger import logger
from ..website.model import AnimeResourceInfo
from .downloader.base import DownloadError
from .task import TERMINAL_STATES, DownloadState, DownloadTask

if TYPE_CHECKING:
    from .downloader.base import BaseDownloader


class DownloadManager:

    def __init__(
        self,
        downloader: BaseDownloader,
        state_file: str = "data/pending_downloads.json",
        poll_interval: float = 60.0,
        max_concurrent: int = 8,
    ):
        self._downloader = downloader
        self.state_file = Path(state_file)
        self.poll_interval = poll_interval
        self._tasks: dict[str, DownloadTask] = {}
        self._tasks_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._background_tasks: set[asyncio.Task[None]] = set()

        self._on_state_change: list[Callable[[DownloadTask, DownloadState], None]] = []
        self._on_complete: list[Callable[[DownloadTask], None]] = []
        self._on_error: list[Callable[[DownloadTask, str], None]] = []

        self._load_state()
        logger.info(f"Initialized with {type(downloader).__name__}")

        self._schedule_recovered_tasks_if_possible()

    # ── Public API ───────────────────────────────────────────────────

    @property
    def downloader(self) -> BaseDownloader:
        return self._downloader

    def is_downloading(self, resource_info: AnimeResourceInfo) -> bool:
        """Check if a resource is currently downloading."""
        return any(
            t.resource_info.download_url == resource_info.download_url
            for t in self._tasks.values()
        )

    def list_tasks(self) -> list[DownloadTask]:
        """Return all active download tasks."""
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> DownloadTask | None:
        """Get a task by ID, or None if not found."""
        return self._tasks.get(task_id)

    def on_complete(self, callback: Callable[[DownloadTask], None]) -> None:
        """Register a callback for successful download completion.

        Args:
            callback: Sync or async function receiving the completed task.
        """
        self._on_complete.append(callback)

    def on_error(self, callback: Callable[[DownloadTask, str], None]) -> None:
        """Register a callback for download failure.

        Args:
            callback: Function receiving the failed task and error message.
        """
        self._on_error.append(callback)

    async def download(self, resource_info: AnimeResourceInfo, save_path: str) -> bool:
        """Create and process a download task (blocking).

        Waits until the task reaches a terminal state before returning.

        Returns:
            True if the download completed successfully.
        """
        task = DownloadTask.from_resource_info(resource_info, base_path=save_path)

        async with self._tasks_lock:
            self._tasks[task.id] = task
        self._save_state()

        await self._process_task(task)
        return task.state == DownloadState.COMPLETED

    async def submit(
        self, resource_info: AnimeResourceInfo, save_path: str
    ) -> DownloadTask:
        """Create a download task and process it in the background (non-blocking).

        Returns:
            The newly created DownloadTask (state updates in-place).
        """
        task = DownloadTask.from_resource_info(resource_info, base_path=save_path)

        async with self._tasks_lock:
            self._tasks[task.id] = task
        self._save_state()

        self._spawn_background(task)
        return task

    # ── State persistence ────────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted tasks from state file."""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for task_id, task_data in data.items():
                task = DownloadTask.from_dict(task_data)
                if task.state not in TERMINAL_STATES:
                    self._tasks[task_id] = task

            if self._tasks:
                logger.info(f"Resuming {len(self._tasks)} pending download(s)")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            self._tasks = {}

    def _save_state(self) -> None:
        """Persist active (non-terminal) tasks to state file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                tid: task.to_dict()
                for tid, task in self._tasks.items()
                if task.state not in TERMINAL_STATES
            }

            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    # ── Task lifecycle ───────────────────────────────────────────────

    def _schedule_recovered_tasks_if_possible(self) -> None:
        """Schedule recovered tasks only when running inside an active loop."""
        if not self._tasks:
            return

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(
                "Skip auto-resume scheduling: no running event loop "
                "during DownloadManager initialization"
            )
            return

        for task in self._tasks.values():
            if task.state not in TERMINAL_STATES:
                self._spawn_background(task)

    def _spawn_background(self, task: DownloadTask) -> None:
        """Create a background asyncio.Task for processing a download task."""
        bg = asyncio.create_task(self._process_task(task))
        self._background_tasks.add(bg)
        bg.add_done_callback(self._background_tasks.discard)

    async def _process_task(self, task: DownloadTask) -> None:
        async with self._semaphore:
            await self._run_download(task)

    async def _run_download(self, task: DownloadTask) -> None:
        """Execute the download lifecycle for a single task."""
        if task.state == DownloadState.PENDING:
            logger.info(f"Starting download: {task.resource_info.title}")
            task.update_state(DownloadState.DOWNLOADING)
            task.started_at = datetime.now().isoformat()
            self._save_state()
            self._emit_state_change(task, DownloadState.DOWNLOADING)

        try:
            await self._downloader.download(task)
        except asyncio.CancelledError:
            task.update_state(DownloadState.CANCELLED)
            self._save_state()
            await self._finalize_task(task, success=False)
            raise
        except Exception as e:
            error_msg = str(e)
            if not isinstance(e, DownloadError):
                logger.exception(f"Unexpected error during download: {e}")
            task.mark_failed(error_msg)
            self._save_state()
            await self._handle_failure(task)
            return

        task.update_state(DownloadState.COMPLETED)
        task.completed_at = datetime.now().isoformat()
        self._save_state()
        self._emit_state_change(task, DownloadState.COMPLETED)
        logger.info(f"Download completed: {task.output_path}")
        await self._finalize_task(task, success=True)

    async def _handle_failure(self, task: DownloadTask) -> None:
        """Handle task failure with retry logic."""
        if task.can_retry():
            logger.warning(
                f"Task failed (attempt {task.retry_count}/"
                f"{task.max_retries}), msg: {task.error_message}"
                f", retrying: {task.resource_info.title}"
            )
            task.retry()
            self._save_state()
            await self._run_download(task)
        else:
            logger.error(
                f"Task failed after {task.retry_count} retries"
                f", msg: {task.error_message}"
                f", title: {task.resource_info.title}"
            )
            await self._finalize_task(task, success=False)

    # ── Finalization & callbacks ─────────────────────────────────────

    def _emit_state_change(self, task: DownloadTask, new_state: DownloadState) -> None:
        for callback in self._on_state_change:
            try:
                callback(task, new_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")

    async def _finalize_task(self, task: DownloadTask, *, success: bool) -> None:
        """Finalize a task and remove it from the active task map."""
        self._save_state()
        await self._run_finalize_callbacks(task, success)
        await self._remove_task(task, success)

    async def _run_finalize_callbacks(self, task: DownloadTask, success: bool) -> None:
        callbacks = self._on_complete if success else self._on_error
        error_message = task.error_message or "Unknown error"

        for callback in callbacks:
            try:
                result = callback(task) if success else callback(task, error_message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _remove_task(self, task: DownloadTask, success: bool) -> None:
        async with self._tasks_lock:
            if task.id in self._tasks:
                del self._tasks[task.id]
                logger.debug(
                    f"Task finalized and removed: {task.id} " f"(success={success})"
                )
