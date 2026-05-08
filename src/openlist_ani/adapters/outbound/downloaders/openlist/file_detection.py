"""Downloaded file detection for OpenList temporary directories."""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable

from openlist_ani.integrations.openlist import OpenListClient
from openlist_ani.logger import logger

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
    _, ext = os.path.splitext(name)
    return ext.lower() in _VIDEO_EXTENSIONS


class OpenListFileDetector:
    """Find the most likely downloaded video in an OpenList directory."""

    def __init__(
        self,
        client: OpenListClient,
        sleep: Callable[[float], Awaitable[None]],
        timeout_seconds: float = 30,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds

    async def detect(
        self,
        temp_path: str,
    ) -> str | None:
        start_time = time.monotonic()
        logger.debug(f"Detecting downloaded file in {temp_path}")

        while True:
            candidates = await self._collect_video_files(temp_path, "")
            if candidates:
                candidates.sort(key=lambda item: item[1], reverse=True)
                selected = candidates[0][0]
                logger.debug(
                    f"Detected downloaded file in {temp_path}: "
                    f"{selected} ({len(candidates)} video candidate(s))"
                )
                return selected

            if time.monotonic() - start_time >= self._timeout_seconds:
                logger.debug(f"Downloaded file detection timed out in {temp_path}")
                return None

            await self._sleep(10)

    async def _collect_video_files(
        self,
        current_path: str,
        relative_prefix: str,
    ) -> list[tuple[str, int]]:
        files = await self._client.list_files(current_path)
        if not files:
            logger.debug(f"No files found while scanning {current_path}")
            return []

        candidates: list[tuple[str, int]] = []
        for file_info in files:
            name = file_info.name
            relative_name = f"{relative_prefix}/{name}" if relative_prefix else name

            if file_info.is_dir:
                next_path = f"{current_path.rstrip('/')}/{name}"
                candidates.extend(
                    await self._collect_video_files(next_path, relative_name)
                )
                continue

            if _is_video_file(name):
                size = file_info.size if isinstance(file_info.size, int) else 0
                candidates.append((relative_name, size))

        return candidates
