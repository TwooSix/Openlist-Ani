"""
OpenList downloader implementation.

This module provides the OpenListDownloader class which implements
the BaseDownloader interface for downloading via OpenList's offline
download functionality.
"""

import asyncio
import os
import re
from typing import Optional

from openlist_ani.logger import logger

from ..model.task import DownloadTask
from .api.model import OfflineDownloadTool, OpenlistTaskState
from .api.openlist import OpenListClient
from .base import BaseDownloader, HandlerResult


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in filenames."""
    # Invalid chars for Windows: < > : " / \ | ? *
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, " ", name)
    sanitized = sanitized.strip()
    return sanitized


def format_anime_episode(
    anime_name: Optional[str], season: Optional[int], episode: Optional[int]
) -> str:
    """Safely format anime episode info, handling None values."""
    name = anime_name or "Unknown"
    season_str = f"S{season:02d}" if season is not None else "S??"
    episode_str = f"E{episode:02d}" if episode is not None else "E??"
    return f"{name} {season_str}{episode_str}"


class OpenListDownloader(BaseDownloader):
    """
    Downloader implementation using OpenList's offline download API.

    This downloader:
    - Creates temp directories for downloads
    - Uses OpenList's offline download (aria2, qBittorrent, etc.)
    - Monitors download progress via task status
    - Renames and moves files to final location
    """

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
        self._client: Optional[OpenListClient] = None

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

    async def on_pending(self, task: DownloadTask) -> HandlerResult:
        logger.debug(f"Preparing: {task.resource_info.title}")

        temp_dir_name = task.id
        temp_path = f"{task.save_path.rstrip('/')}/{temp_dir_name}"

        logger.debug(f"Creating temporary directory: {temp_path}")
        if not await self.client.mkdir(temp_path):
            return HandlerResult.fail(
                f"Failed to create temporary directory: {temp_path}"
            )

        files = await self.client.list_files(temp_path)
        task.initial_files = [f.name for f in files] if files else []
        task.temp_path = temp_path

        logger.debug(f"  Title: {task.resource_info.title}")
        logger.debug(f"  URL: {task.resource_info.download_url}")
        logger.debug(f"  Temp path: {temp_path}")

        tasks = await self.client.add_offline_download(
            urls=[task.resource_info.download_url],
            path=temp_path,
            tool=self._offline_download_tool,
        )

        if not tasks:
            return HandlerResult.fail("Failed to create offline download task")

        task.extra_data["task_id"] = tasks[0].id
        logger.debug(f"Download task created with ID: {tasks[0].id}")

        return HandlerResult.done()

    async def on_downloading(self, task: DownloadTask) -> HandlerResult:
        task_id = task.extra_data.get("task_id")
        if not task_id:
            return HandlerResult.fail("No task ID available")

        undone_result = await self._check_undone_tasks(task, task_id)
        if undone_result is not None:
            return undone_result

        done_result = await self._check_done_tasks(task, task_id)
        if done_result is not None:
            return done_result

        return HandlerResult.fail(f"Task {task_id} not found")

    async def _check_undone_tasks(
        self, task: DownloadTask, task_id: str
    ) -> Optional[HandlerResult]:
        undone_tasks = await self.client.get_offline_download_undone()
        if undone_tasks is None:
            return HandlerResult.fail("Failed to fetch undone tasks")

        for api_task in undone_tasks:
            if api_task.id != task_id:
                continue

            progress = float(api_task.progress) if api_task.progress else None
            self._log_download_progress(task, progress)
            return HandlerResult.poll()

        return None

    async def _check_done_tasks(
        self, task: DownloadTask, task_id: str
    ) -> Optional[HandlerResult]:
        done_tasks = await self.client.get_offline_download_done()
        if done_tasks is None:
            return HandlerResult.fail("Failed to fetch done tasks")

        for api_task in done_tasks:
            if api_task.id != task_id:
                continue
            return self._resolve_done_task(task, api_task.state)

        return None

    async def _resolve_done_task(
        self, task: DownloadTask, task_state: OpenlistTaskState
    ) -> HandlerResult:
        if task_state != OpenlistTaskState.Succeeded:
            logger.error(f"Download failed with state: {task_state}")
            return HandlerResult.fail(f"Task failed with state: {task_state}")

        logger.debug(
            f"Download finished, detecting file for: {task.resource_info.title}"
        )
        downloaded_filename = await self._detect_downloaded_file(task)
        if not downloaded_filename:
            return HandlerResult.fail("Download completed but no file found")

        task.downloaded_filename = downloaded_filename
        return HandlerResult.done()

    def _log_download_progress(
        self, task: DownloadTask, progress: Optional[float]
    ) -> None:
        """Log task progress using milestone info logs and debug fallback."""
        if progress is None:
            return

        if progress > 0 and int(progress) % 25 == 0:
            logger.info(
                f"Downloading [{format_anime_episode(task.resource_info.anime_name, task.resource_info.season, task.resource_info.episode)}]: {progress:.0f}%"
            )
            return

        logger.debug(f"Progress: {progress:.1f}%")

    async def _detect_downloaded_file(self, task: DownloadTask) -> Optional[str]:
        """Detect the downloaded file in the temp directory."""
        if not task.temp_path:
            return None

        files = await self.client.list_files(task.temp_path)
        if not files:
            return None

        initial_files = set(task.initial_files)

        for file_info in files:
            name = file_info.name
            # Skip incomplete download markers
            if name.endswith(".aria2") or name.endswith(".downloading"):
                continue
            # Check if it's a new file
            if name not in initial_files:
                return name

        return None

    async def on_transferring(self, task: DownloadTask) -> HandlerResult:
        logger.debug(f"Transferring: {task.resource_info.title}")

        if not task.downloaded_filename:
            return HandlerResult.fail("No downloaded filename available")
        if not task.temp_path:
            return HandlerResult.fail("No temp_path available")

        anime_name = sanitize_filename(task.resource_info.anime_name or "Unknown")
        season = task.resource_info.season or 1
        episode = task.resource_info.episode or 1
        final_dir_path = self._build_final_dir_path(task, anime_name, season)
        final_filename = self._build_final_filename(task, anime_name, season, episode)

        if not await self.client.mkdir(final_dir_path):
            return HandlerResult.fail(f"Failed to create directory: {final_dir_path}")

        file_to_move = await self._rename_temp_file_if_needed(task, final_filename)

        logger.debug(
            f"Moving file to final destination: {final_dir_path}/{file_to_move}"
        )
        if not await self.client.move_file(
            task.temp_path, final_dir_path, [file_to_move]
        ):
            return HandlerResult.fail(f"Failed to move file to: {final_dir_path}")

        task.final_path = f"{final_dir_path}/{file_to_move}"
        return HandlerResult.done()

    def _build_final_dir_path(
        self, task: DownloadTask, anime_name: str, season: int
    ) -> str:
        """Build final destination directory path."""
        season_dir = f"Season {season}"
        final_dir_path = f"{task.save_path.rstrip('/')}/{anime_name}/{season_dir}"
        return final_dir_path

    def _build_final_filename(
        self,
        task: DownloadTask,
        anime_name: str,
        season: int,
        episode: int,
    ) -> str:
        """Build final filename using configured rename format and source extension."""
        downloaded_filename = task.downloaded_filename or ""
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

        try:
            final_filename_stem = self._rename_format.format(**rename_context).strip()
        except Exception as e:
            logger.warning(
                f"Failed to format filename using format string: '{self._rename_format}'. "
                f"Error: {e}. Falling back to default."
            )
            final_filename_stem = f"{anime_name} S{season:02d}E{episode:02d}"

        if version > 1:
            final_filename_stem = f"{final_filename_stem} v{version}"

        return f"{final_filename_stem}{ext}".strip()

    async def _rename_temp_file_if_needed(
        self, task: DownloadTask, final_filename: str
    ) -> str:
        """Rename file in temp directory when target name differs."""
        downloaded_filename = task.downloaded_filename or ""
        if final_filename == downloaded_filename:
            return downloaded_filename

        logger.debug(f"Renaming file to: {final_filename}")
        temp_file_path = f"{task.temp_path}/{downloaded_filename}"
        if await self.client.rename_file(temp_file_path, final_filename):
            logger.debug("Waiting for remote server cache to refresh...")
            await asyncio.sleep(5)
            logger.debug(f"Renamed {downloaded_filename} to {final_filename}")
            return final_filename

        logger.warning(
            f"Rename failed, will move with original name: {downloaded_filename}"
        )
        return downloaded_filename

    async def on_cleaning_up(self, task: DownloadTask) -> HandlerResult:
        await self._cleanup(task)
        return HandlerResult.done()

    async def on_failed(self, task: DownloadTask) -> None:
        await self._cleanup(task)

    async def _cleanup(self, task: DownloadTask) -> bool:
        """Clean up temporary directory."""
        if not task.temp_path:
            return True

        temp_dir_name = task.id
        logger.debug(f"Cleaning up temporary directory: {temp_dir_name}")

        return await self.client.remove_path(task.save_path, [temp_dir_name])
