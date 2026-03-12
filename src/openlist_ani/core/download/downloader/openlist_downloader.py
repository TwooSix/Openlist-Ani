"""
OpenList downloader implementation.

This module provides the OpenListDownloader class which implements
the BaseDownloader interface for downloading via OpenList's offline
download functionality.
"""

import asyncio
import os
import re
import time

from ....logger import logger
from ..api.client import OpenListClient
from ..api.model import OfflineDownloadTool, OpenlistTaskState
from ..task import DownloadTask
from .base import BaseDownloader, DownloadError


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in filenames."""
    # Invalid chars for Windows: < > : " / \ | ? *
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, " ", name)
    sanitized = sanitized.strip()
    return sanitized


# list of video file extensions we consider when detecting downloads
_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".flv",
    ".wmv",
    ".webm",
    ".mpg",
    ".mpeg",
}


def _is_video_file(name: str) -> bool:
    """Return True if the filename has a recognised video extension."""
    _, ext = os.path.splitext(name)
    return ext.lower() in _VIDEO_EXTENSIONS


def format_anime_episode(
    anime_name: str | None, season: int | None, episode: int | None
) -> str:
    """Safely format anime episode info, handling None values."""
    name = anime_name or "Unknown"
    season_str = f"S{season:02d}" if season is not None else "S??"
    episode_str = f"E{episode:02d}" if episode is not None else "E??"
    return f"{name} {season_str}{episode_str}"


class OpenListDownloader(BaseDownloader):
    """
    Downloader implementation using OpenList's offline download API.

    This downloader handles the complete lifecycle:
    1. Create temp directory and submit offline download
    2. Poll until download completes
    3. Wait for transfer task (if applicable)
    4. Detect, rename, and move the downloaded file
    5. Clean up temporary resources
    """

    _TRANSFER_CHECK_MAX_RETRIES = 3
    _TRANSFER_CHECK_INTERVAL_SECONDS = 5
    _FILE_DETECT_TIMEOUT_SECONDS = 30

    def __init__(
        self,
        base_url: str,
        token: str,
        offline_download_tool: OfflineDownloadTool | str,
        rename_format: str,
    ):
        if not base_url:
            raise ValueError("base_url is required")
        if offline_download_tool is None:
            raise ValueError("offline_download_tool is required")
        if rename_format is None:
            raise ValueError("rename_format is required")

        self._base_url = base_url
        self._token = token
        self._offline_download_tool = offline_download_tool
        self._rename_format = rename_format
        self._client: OpenListClient | None = None

    @property
    def client(self) -> OpenListClient:
        """Lazy-initialize the OpenList client."""
        if self._client is None:
            self._client = OpenListClient(
                base_url=self._base_url,
                token=self._token,
            )
        return self._client

    @property
    def downloader_type(self) -> str:
        return "openlist"

    # ── Main entry point ─────────────────────────────────────────────

    async def download(self, task: DownloadTask) -> None:
        """Execute the complete OpenList download lifecycle.

        This method is idempotent — safe to call again after a restart.
        It checks task.downloader_data and the OpenList backend to determine
        the current state and resumes from there.
        """
        try:
            await self._submit_download(task)
            await self._wait_download_complete(task)
            await self._wait_transfer_complete(task)

            if not task.output_path:
                downloaded = await self._detect_downloaded_file(task)
                if not downloaded:
                    raise DownloadError("Could not detect downloaded file")
                task.downloader_data["downloaded_filename"] = downloaded
                await self._transfer_to_final(task)

        except asyncio.CancelledError:
            raise
        except DownloadError:
            raise
        except Exception as e:
            raise DownloadError(str(e)) from e
        finally:
            await self._safe_cleanup(task)

    # ── Step 1: Submit download (idempotent) ─────────────────────────

    async def _submit_download(self, task: DownloadTask) -> None:
        """Create temp dir and submit offline download.

        If task.downloader_data already has a task_id, this is a no-op (idempotent).
        """
        if task.downloader_data.get("task_id"):
            logger.debug(
                f"Download already submitted: {task.downloader_data['task_id']}"
            )
            return

        logger.debug(f"Preparing: {task.resource_info.title}")

        temp_dir_name = task.id
        temp_path = f"{task.base_path.rstrip('/')}/{temp_dir_name}"

        logger.debug(f"Creating temporary directory: {temp_path}")
        if not await self.client.mkdir(temp_path):
            raise DownloadError(f"Failed to create temporary directory: {temp_path}")

        files = await self.client.list_files(temp_path)
        task.downloader_data["initial_files"] = [f.name for f in files] if files else []
        task.downloader_data["temp_path"] = temp_path

        logger.debug(f"  Title: {task.resource_info.title}")
        logger.debug(f"  URL: {task.resource_info.download_url}")
        logger.debug(f"  Temp path: {temp_path}")

        tasks = await self.client.add_offline_download(
            urls=[task.resource_info.download_url],
            path=temp_path,
            tool=self._offline_download_tool,
        )

        if not tasks:
            raise DownloadError("Failed to create offline download task")

        task.downloader_data["task_id"] = tasks[0].id
        logger.debug(f"Download task created with ID: {tasks[0].id}")

    # ── Step 2: Wait for offline download ────────────────────────────

    async def _wait_download_complete(self, task: DownloadTask) -> None:
        """Poll until the offline download completes."""
        task_id = task.downloader_data.get("task_id")
        if not task_id:
            raise DownloadError("No task ID available")

        while True:
            undone = await self.client.get_offline_download_undone()
            if undone is None:
                raise DownloadError("Failed to fetch undone download tasks")

            matching = next((t for t in undone if t.id == task_id), None)
            if matching is not None:
                progress = float(matching.progress) if matching.progress else None
                self._log_progress(task, progress, is_transfer=False)
                await asyncio.sleep(5)
                continue

            done = await self.client.get_offline_download_done()
            if done is None:
                raise DownloadError("Failed to fetch done download tasks")

            matching_done = next((t for t in done if t.id == task_id), None)
            if matching_done is not None:
                if matching_done.state != OpenlistTaskState.SUCCEEDED:
                    logger.error(f"Download failed with state: {matching_done.state}")
                    raise DownloadError(
                        f"Task failed with state: {matching_done.state}"
                    )
                return

            raise DownloadError(f"Task {task_id} not found in undone or done lists")

    # ── Step 3: Wait for transfer task ───────────────────────────────

    async def _wait_transfer_complete(self, task: DownloadTask) -> None:
        """Wait for a transfer task to complete, if one exists."""
        task_uuid = task.id
        not_found_count = 0

        while True:
            undone = await self.client.get_offline_download_transfer_undone()
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
                await asyncio.sleep(self._TRANSFER_CHECK_INTERVAL_SECONDS)
                continue

            done = await self.client.get_offline_download_transfer_done()
            if done is None:
                raise DownloadError("Failed to fetch done transfer tasks")

            matching_done = next((t for t in done if task_uuid in t.name), None)
            if matching_done is not None:
                if matching_done.state != OpenlistTaskState.SUCCEEDED:
                    logger.error(f"Transfer failed with state: {matching_done.state}")
                    raise DownloadError(
                        f"Transfer failed with state: {matching_done.state}"
                    )
                return

            not_found_count += 1
            if not_found_count >= self._TRANSFER_CHECK_MAX_RETRIES:
                logger.debug(
                    f"No transfer task for {task_uuid} after "
                    f"{self._TRANSFER_CHECK_MAX_RETRIES} checks, skipping"
                )
                return

            await asyncio.sleep(self._TRANSFER_CHECK_INTERVAL_SECONDS)

    # ── Step 4: Detect downloaded file ───────────────────────────────

    async def _detect_downloaded_file(self, task: DownloadTask) -> str | None:
        """Detect the downloaded file in the temp directory."""
        temp_path = task.downloader_data.get("temp_path")
        if not temp_path:
            return None

        start_time = time.monotonic()
        initial_files = set(task.downloader_data.get("initial_files", []))

        while True:
            candidates = await self._collect_video_files(temp_path, "", initial_files)

            if candidates:
                candidates.sort(key=lambda item: item[1], reverse=True)
                return candidates[0][0]

            elapsed = time.monotonic() - start_time
            if elapsed >= self._FILE_DETECT_TIMEOUT_SECONDS:
                return None

            await asyncio.sleep(10)

    async def _collect_video_files(
        self,
        current_path: str,
        relative_prefix: str,
        initial_files: set[str],
    ) -> list[tuple[str, int]]:
        """Recursively collect video files with their sizes."""
        files = await self.client.list_files(current_path)
        if not files:
            return []

        candidates: list[tuple[str, int]] = []
        for file_info in files:
            name = file_info.name
            relative_name = f"{relative_prefix}/{name}" if relative_prefix else name

            if file_info.is_dir:
                next_path = f"{current_path.rstrip('/')}/{name}"
                candidates.extend(
                    await self._collect_video_files(
                        next_path, relative_name, initial_files
                    )
                )
                continue

            if _is_video_file(name) and relative_name not in initial_files:
                size = file_info.size if isinstance(file_info.size, int) else 0
                candidates.append((relative_name, size))

        return candidates

    # ── Step 5: Transfer to final location ───────────────────────────

    async def _transfer_to_final(self, task: DownloadTask) -> None:
        """Rename and move the downloaded file to its final location."""
        downloaded_filename = task.downloader_data.get("downloaded_filename")
        temp_path = task.downloader_data.get("temp_path")
        if not downloaded_filename:
            raise DownloadError("No downloaded filename available")
        if not temp_path:
            raise DownloadError("No temp_path available")

        anime_name = sanitize_filename(task.resource_info.anime_name or "Unknown")
        season = task.resource_info.season or 1
        episode = task.resource_info.episode or 1
        final_dir_path = self._build_final_dir_path(task, anime_name, season)
        final_filename = self._build_final_filename(task, anime_name, season, episode)

        if not await self.client.mkdir(final_dir_path):
            raise DownloadError(f"Failed to create directory: {final_dir_path}")

        file_to_move = await self._rename_temp_file_if_needed(task, final_filename)

        logger.debug(
            f"Moving file to final destination: " f"{final_dir_path}/{file_to_move}"
        )
        if not await self.client.move_file(temp_path, final_dir_path, [file_to_move]):
            raise DownloadError(f"Failed to move file to: {final_dir_path}")

        task.output_path = f"{final_dir_path}/{file_to_move}"

    # ── Cleanup ──────────────────────────────────────────────────────

    async def _safe_cleanup(self, task: DownloadTask) -> None:
        """Clean up temporary directory, swallowing errors."""
        if not task.downloader_data.get("temp_path"):
            return
        try:
            temp_dir_name = task.id
            logger.debug(f"Cleaning up temporary directory: {temp_dir_name}")
            await self.client.remove_path(task.base_path, [temp_dir_name])
        except Exception as e:
            logger.warning(f"Cleanup failed for {task.id}: {e}")

    # ── Helpers ──────────────────────────────────────────────────────

    def _log_progress(
        self, task: DownloadTask, progress: float | None, is_transfer: bool = False
    ) -> None:
        """Log progress once per 25% bucket, with debug fallback."""
        if progress is None:
            return

        bounded_progress = max(0.0, min(progress, 100.0))
        task.progress = bounded_progress
        bucket_size = 25
        bucket_index = min(int(bounded_progress // bucket_size), 3)
        bucket_key = (
            "_transfer_progress_bucket" if is_transfer else "_download_progress_bucket"
        )
        last_bucket = task.downloader_data.get(bucket_key)

        if last_bucket != bucket_index:
            task.downloader_data[bucket_key] = bucket_index
            logger.info(
                f"{'Transferring' if is_transfer else 'Downloading'} "
                f"[{format_anime_episode(task.resource_info.anime_name, task.resource_info.season, task.resource_info.episode)}]"
                f": {progress:.0f}%"
            )

    def _build_final_dir_path(
        self, task: DownloadTask, anime_name: str, season: int
    ) -> str:
        """Build final destination directory path."""
        season_dir = f"Season {season}"
        return f"{task.base_path.rstrip('/')}/{anime_name}/{season_dir}"

    def _build_final_filename(
        self,
        task: DownloadTask,
        anime_name: str,
        season: int,
        episode: int,
    ) -> str:
        """Build final filename using configured rename format."""
        downloaded_filename = task.downloader_data.get("downloaded_filename", "")
        _, ext = os.path.splitext(downloaded_filename)
        if ext == "":
            ext = ".mp4"

        rename_context = vars(task.resource_info).copy()
        rename_context["anime_name"] = anime_name
        rename_context.pop("title", None)
        version = rename_context.pop("version", 1) or 1

        quality = rename_context.get("quality")
        if quality is not None:
            rename_context["quality"] = str(quality)
        if isinstance(rename_context.get("languages"), list):
            rename_context["languages"] = "".join(
                str(lang) for lang in rename_context["languages"]
            )

        # Replace None values with empty strings to avoid "None" in filenames
        for key, value in rename_context.items():
            if value is None:
                rename_context[key] = ""

        try:
            final_filename_stem = self._rename_format.format(**rename_context).strip()
        except Exception as e:
            logger.warning(
                f"Failed to format filename using format string: "
                f"'{self._rename_format}'. Error: {e}. "
                f"Falling back to default."
            )
            final_filename_stem = f"{anime_name} S{season:02d}E{episode:02d}"

        if version > 1:
            final_filename_stem = f"{final_filename_stem} v{version}"

        return f"{final_filename_stem}{ext}".strip()

    async def _rename_temp_file_if_needed(
        self, task: DownloadTask, final_filename: str
    ) -> str:
        """Rename file in temp directory when target name differs."""
        downloaded_filename = task.downloader_data.get("downloaded_filename", "")
        if final_filename == downloaded_filename:
            return downloaded_filename

        temp_path = task.downloader_data.get("temp_path", "")
        logger.debug(f"Renaming file to: {final_filename}")
        temp_file_path = f"{temp_path}/{downloaded_filename}"
        if await self.client.rename_file(temp_file_path, final_filename):
            logger.debug("Waiting for remote server cache to refresh...")
            await asyncio.sleep(5)
            logger.debug(f"Renamed {downloaded_filename} to {final_filename}")
            return final_filename

        logger.warning(
            f"Rename failed, will move with original name: " f"{downloaded_filename}"
        )
        return downloaded_filename
