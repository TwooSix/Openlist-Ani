"""OpenList offline-download command processor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum

from openlist_ani.domain.anime_release import format_anime_episode
from openlist_ani.domain.download_task.downloader import DownloadError
from openlist_ani.domain.download_task.task import DownloadTask
from openlist_ani.integrations.openlist import (
    OfflineDownloadTool,
    OpenListClient,
    OpenListFileConflictResolver,
    OpenlistTask,
    OpenlistTaskState,
)
from openlist_ani.logger import logger

from .file_detection import OpenListFileDetector
from .task_snapshot_cache import OpenListTaskSnapshotCache

OPENLIST_TEMP_ROOT_DIRECTORY_NAME = ".oani-download-tmp"
OPENLIST_WORKFLOW_STATE_KEY = "workflow_state"


class OpenListWorkflowState(StrEnum):
    INIT = "init"
    SUBMITTED = "submitted"
    DOWNLOAD_DONE = "download_done"
    TRANSFER_DONE = "transfer_done"
    FILE_DETECTED = "file_detected"
    FILE_RESOLVED = "file_resolved"
    MOVED = "moved"
    DONE = "done"


class OpenListRemoteTaskFailed(DownloadError):
    """Raised when OpenList marks a remote task as failed."""


WorkflowCheckpoint = Callable[[DownloadTask], Awaitable[None]]


def _join_openlist_path(base_path: str, *parts: str) -> str:
    root = (base_path or "/").rstrip("/") or "/"
    suffix = "/".join(part.strip("/") for part in parts if part.strip("/"))
    if not suffix:
        return root
    if root == "/":
        return f"/{suffix}"
    return f"{root}/{suffix}"


def _temp_root_path(base_path: str) -> str:
    return _join_openlist_path(base_path, OPENLIST_TEMP_ROOT_DIRECTORY_NAME)


def _directory_creation_paths(base_path: str, target_path: str) -> list[str]:
    base = (base_path or "/").rstrip("/") or "/"
    target = (target_path or "/").rstrip("/") or "/"
    if target == base:
        return []

    prefix = "/" if base == "/" else f"{base}/"
    if not target.startswith(prefix):
        return [target]

    relative_path = target[len(prefix) :]
    parts = [part for part in relative_path.split("/") if part]
    return [
        _join_openlist_path(base, *parts[:index]) for index in range(1, len(parts) + 1)
    ]


def _workflow_state(task: DownloadTask) -> OpenListWorkflowState:
    raw_state = task.downloader_data.get(OPENLIST_WORKFLOW_STATE_KEY)
    if raw_state:
        try:
            return OpenListWorkflowState(raw_state)
        except ValueError:
            logger.warning(f"Unknown OpenList workflow state: {raw_state}; restarting")

    if task.downloader_data.get("materialized_filename"):
        return OpenListWorkflowState.DONE
    if task.downloader_data.get("resolved_filename"):
        return OpenListWorkflowState.FILE_RESOLVED
    if task.downloader_data.get("downloaded_filename"):
        return OpenListWorkflowState.FILE_DETECTED
    if task.downloader_data.get("task_id"):
        return OpenListWorkflowState.SUBMITTED
    return OpenListWorkflowState.INIT


class OpenListDownloadWorkflow:
    """Command Processor for one OpenList offline-download lifecycle."""

    _TRANSFER_CHECK_MAX_RETRIES = 3
    _TRANSFER_CHECK_INTERVAL_SECONDS = 5

    def __init__(
        self,
        client: OpenListClient,
        offline_download_tool: OfflineDownloadTool | str,
        file_detector: OpenListFileDetector,
        conflict_resolver: OpenListFileConflictResolver,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        task_snapshot_cache: OpenListTaskSnapshotCache | None = None,
    ) -> None:
        self._client = client
        self._offline_download_tool = offline_download_tool
        self._file_detector = file_detector
        self._conflict_resolver = conflict_resolver
        self._sleep = sleep
        self._task_snapshot_cache = task_snapshot_cache or OpenListTaskSnapshotCache(
            client
        )

    async def run(
        self,
        task: DownloadTask,
        checkpoint: WorkflowCheckpoint | None = None,
    ) -> DownloadTask:
        try:
            while True:
                state = _workflow_state(task)
                if state == OpenListWorkflowState.DONE:
                    return task
                await self._run_state_step(task, state, checkpoint)
        except OpenListRemoteTaskFailed:
            await self._safe_cleanup(task)
            self._reset_remote_task(task)
            await self._checkpoint(task, OpenListWorkflowState.INIT, checkpoint)
            raise
        finally:
            if _workflow_state(task) == OpenListWorkflowState.DONE:
                await self._safe_cleanup(task)

    async def _run_state_step(
        self,
        task: DownloadTask,
        state: OpenListWorkflowState,
        checkpoint: WorkflowCheckpoint | None,
    ) -> None:
        step = {
            OpenListWorkflowState.INIT: (
                self._submit_download,
                OpenListWorkflowState.SUBMITTED,
            ),
            OpenListWorkflowState.SUBMITTED: (
                self._wait_download_complete,
                OpenListWorkflowState.DOWNLOAD_DONE,
            ),
            OpenListWorkflowState.DOWNLOAD_DONE: (
                self._wait_transfer_complete,
                OpenListWorkflowState.TRANSFER_DONE,
            ),
            OpenListWorkflowState.TRANSFER_DONE: (
                self._detect_file,
                OpenListWorkflowState.FILE_DETECTED,
            ),
            OpenListWorkflowState.FILE_DETECTED: (
                self._resolve_file_to_move,
                OpenListWorkflowState.FILE_RESOLVED,
            ),
            OpenListWorkflowState.FILE_RESOLVED: (
                self._move_to_target_directory,
                OpenListWorkflowState.MOVED,
            ),
        }.get(state)
        if step is None:
            await self._checkpoint(task, OpenListWorkflowState.DONE, checkpoint)
            return

        handler, next_state = step
        await handler(task)
        await self._checkpoint(task, next_state, checkpoint)

    async def _checkpoint(
        self,
        task: DownloadTask,
        state: OpenListWorkflowState,
        checkpoint: WorkflowCheckpoint | None,
    ) -> None:
        task.downloader_data[OPENLIST_WORKFLOW_STATE_KEY] = state.value
        if checkpoint is not None:
            await checkpoint(task)

    @staticmethod
    def _reset_remote_task(task: DownloadTask) -> None:
        for key in (
            OPENLIST_WORKFLOW_STATE_KEY,
            "task_id",
            "downloaded_filename",
            "resolved_filename",
            "file_parent_path",
        ):
            task.downloader_data.pop(key, None)

    async def _submit_download(self, task: DownloadTask) -> None:
        if task.downloader_data.get("task_id"):
            logger.debug(
                f"OpenList download already submitted: {task.downloader_data['task_id']}"
            )
            return

        temp_path = _join_openlist_path(_temp_root_path(task.base_path), task.id)
        task.downloader_data["temp_path"] = temp_path

        logger.debug(
            f"OpenList submit: title={task.release.title}, "
            f"url={task.release.download_url}, temp={temp_path}"
        )

        tasks = await self._client.add_offline_download(
            urls=[task.release.download_url],
            path=temp_path,
            tool=self._offline_download_tool,
        )
        if not tasks:
            raise DownloadError("Failed to create offline download task")

        self._task_snapshot_cache.invalidate()
        task.downloader_data["task_id"] = tasks[0].id
        logger.debug(f"OpenList task created: {tasks[0].id}")

    def _ensure_task_succeeded(self, matching_done: OpenlistTask, label: str) -> None:
        if matching_done.state != OpenlistTaskState.SUCCEEDED:
            details = matching_done.error or matching_done.status
            message = f"{label} failed with state: {matching_done.state}"
            if details:
                message = f"{message}: {details}"
            logger.warning(message)
            raise OpenListRemoteTaskFailed(message)

    async def _wait_download_complete(self, task: DownloadTask) -> None:
        task_id = task.downloader_data.get("task_id")
        if not task_id:
            raise DownloadError("No task ID available")

        while True:
            matching = await self._find_undone_download_task(task_id)
            if matching is not None:
                if await self._handle_undone_download_task(task, matching):
                    return
                continue

            if await self._download_task_is_done(task_id):
                return

            raise DownloadError(f"Task {task_id} not found in undone or done lists")

    async def _find_undone_download_task(self, task_id: str) -> OpenlistTask | None:
        undone = await self._task_snapshot_cache.get_offline_download_undone()
        if undone is None:
            raise DownloadError("Failed to fetch undone download tasks")
        return next((t for t in undone if t.id == task_id), None)

    async def _handle_undone_download_task(
        self, task: DownloadTask, matching: OpenlistTask
    ) -> bool:
        progress = float(matching.progress) if matching.progress else None
        self._log_progress(task, progress, is_transfer=False)
        if self._is_complete_progress(progress) and await self._transfer_task_exists(
            task
        ):
            logger.debug(
                f"OpenList download task {matching.id} is still in undone list "
                "at 100%, but transfer task exists; advancing to transfer"
            )
            return True

        await self._sleep(5)
        return False

    async def _download_task_is_done(self, task_id: str) -> bool:
        done = await self._task_snapshot_cache.get_offline_download_done()
        if done is None:
            raise DownloadError("Failed to fetch done download tasks")

        matching_done = next((t for t in done if t.id == task_id), None)
        if matching_done is None:
            return False

        self._ensure_task_succeeded(matching_done, "Task")
        return True

    async def _transfer_task_exists(self, task: DownloadTask) -> bool:
        task_uuid = task.id
        undone = await self._task_snapshot_cache.get_offline_download_transfer_undone()
        if undone is None:
            logger.debug("Could not probe undone transfer tasks")
        elif any(task_uuid in transfer.name for transfer in undone):
            return True

        done = await self._task_snapshot_cache.get_offline_download_transfer_done()
        if done is None:
            logger.debug("Could not probe done transfer tasks")
            return False
        return any(task_uuid in transfer.name for transfer in done)

    @staticmethod
    def _is_complete_progress(progress: float | None) -> bool:
        return progress is not None and progress >= 100.0

    async def _wait_transfer_complete(self, task: DownloadTask) -> None:
        task_uuid = task.id
        not_found_count = 0

        while True:
            undone = (
                await self._task_snapshot_cache.get_offline_download_transfer_undone()
            )
            if undone is None:
                raise DownloadError("Failed to fetch undone transfer tasks")

            matching_undone = next((t for t in undone if task_uuid in t.name), None)
            if matching_undone is not None:
                progress = (
                    float(matching_undone.progress)
                    if matching_undone.progress
                    else None
                )
                self._log_progress(task, progress, is_transfer=True)
                not_found_count = 0
                await self._sleep(self._TRANSFER_CHECK_INTERVAL_SECONDS)
                continue

            done = await self._task_snapshot_cache.get_offline_download_transfer_done()
            if done is None:
                raise DownloadError("Failed to fetch done transfer tasks")

            matching_done = next((t for t in done if task_uuid in t.name), None)
            if matching_done is not None:
                self._ensure_task_succeeded(matching_done, "Transfer")
                return

            not_found_count += 1
            if not_found_count >= self._TRANSFER_CHECK_MAX_RETRIES:
                logger.debug(
                    f"No transfer task for {task_uuid} after "
                    f"{self._TRANSFER_CHECK_MAX_RETRIES} checks, skipping"
                )
                return

            await self._sleep(self._TRANSFER_CHECK_INTERVAL_SECONDS)

    async def _detect_file(self, task: DownloadTask) -> None:
        temp_path = task.downloader_data.get("temp_path")
        if not temp_path:
            raise DownloadError("No temp_path available")

        downloaded = await self._file_detector.detect(temp_path)
        if not downloaded:
            raise DownloadError("Could not detect downloaded file")

        task.downloader_data["downloaded_filename"] = downloaded

    async def _resolve_file_to_move(self, task: DownloadTask) -> None:
        downloaded_filename = task.downloader_data.get("downloaded_filename")
        temp_path = task.downloader_data.get("temp_path")
        if not downloaded_filename:
            raise DownloadError("No downloaded filename available")
        if not temp_path:
            raise DownloadError("No temp_path available")

        if "/" in downloaded_filename:
            sub_dir, bare_filename = downloaded_filename.rsplit("/", 1)
            file_parent_path = f"{temp_path.rstrip('/')}/{sub_dir}"
        else:
            bare_filename = downloaded_filename
            file_parent_path = temp_path

        final_dir_path = task.target_directory_path
        if not final_dir_path:
            raise DownloadError("No target directory path available")
        for directory_path in _directory_creation_paths(task.base_path, final_dir_path):
            if not await self._client.mkdir(directory_path):
                raise DownloadError(f"Failed to create directory: {directory_path}")

        file_to_move = await self._conflict_resolver.resolve_before_move(
            file_parent_path, final_dir_path, bare_filename
        )
        task.downloader_data["file_parent_path"] = file_parent_path
        task.downloader_data["resolved_filename"] = file_to_move

    async def _move_to_target_directory(self, task: DownloadTask) -> None:
        file_parent_path = task.downloader_data.get("file_parent_path")
        file_to_move = task.downloader_data.get("resolved_filename")
        if not file_parent_path:
            raise DownloadError("No file_parent_path available")
        if not file_to_move:
            raise DownloadError("No resolved_filename available")

        final_dir_path = task.target_directory_path
        if not final_dir_path:
            raise DownloadError("No target directory path available")
        logger.debug(
            f"OpenList move: {file_parent_path}/{file_to_move} -> {final_dir_path}"
        )
        if not await self._client.move_file(
            file_parent_path, final_dir_path, [file_to_move]
        ):
            raise DownloadError(f"Failed to move file to: {final_dir_path}")

        await self._sleep(self._TRANSFER_CHECK_INTERVAL_SECONDS)

        task.output_path = f"{final_dir_path}/{file_to_move}"
        task.downloader_data["materialized_directory_path"] = final_dir_path
        task.downloader_data["materialized_filename"] = file_to_move

    async def _safe_cleanup(self, task: DownloadTask) -> None:
        if not task.downloader_data.get("temp_path"):
            return
        try:
            logger.debug(f"Cleaning up temporary directory: {task.id}")
            await self._client.remove_path(_temp_root_path(task.base_path), [task.id])
        except Exception as e:
            logger.warning(f"Cleanup failed for {task.id}: {e}")

    def _log_progress(
        self, task: DownloadTask, progress: float | None, is_transfer: bool = False
    ) -> None:
        if progress is None:
            return

        bounded_progress = max(0.0, min(progress, 100.0))
        task.progress = bounded_progress
        bucket_size = 25
        bucket_index = min(int(bounded_progress // bucket_size), 4)
        if bucket_index == 0:
            return
        bucket_key = (
            "_transfer_progress_bucket" if is_transfer else "_download_progress_bucket"
        )
        last_bucket = task.downloader_data.get(bucket_key)

        if last_bucket != bucket_index:
            task.downloader_data[bucket_key] = bucket_index
            label = format_anime_episode(
                task.release.anime_name,
                task.release.season,
                task.release.episode,
            )
            task_ref = task.id[:8]
            openlist_task_id = task.downloader_data.get("task_id", "unknown")
            logger.info(
                f"{'Transferring' if is_transfer else 'Downloading'} "
                f"[{label}]: {bucket_index * bucket_size}% "
                f"(task={task_ref}, openlist_task={openlist_task_id})"
            )
