"""OpenList filename conflict resolution helpers."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from openlist_ani.logger import logger

from .client import OpenListClient


class OpenListFileConflictError(Exception):
    """Raised when OpenList storage cannot resolve a destination filename."""


class OpenListFileConflictResolver:
    """Resolve destination filename conflicts before move or rename commands."""

    _MAX_CONFLICT_SUFFIX = 99

    def __init__(
        self,
        client: OpenListClient,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._client = client
        self._sleep = sleep

    async def resolve_before_move(
        self,
        temp_path: str,
        final_dir_path: str,
        filename: str,
    ) -> str:
        existing_files = await self._client.list_files(final_dir_path)
        if existing_files is None:
            return filename

        existing_names = {f.name for f in existing_files}
        if filename not in existing_names:
            return filename

        # OpenList move cannot supply a destination name, so deduplicate the
        # temporary source file before moving it into the target directory.
        new_filename = self._next_available_name(
            filename,
            existing_names,
        )
        temp_file_path = f"{temp_path}/{filename}"
        if not await self._client.rename_file(temp_file_path, new_filename):
            temp_files = await self._client.list_files(temp_path)
            temp_names = {f.name for f in temp_files or []}
            if new_filename in temp_names:
                logger.debug(
                    f"Conflict-resolved temp file already exists: {new_filename}"
                )
                return new_filename
            raise OpenListFileConflictError(
                f"Failed to rename '{filename}' to '{new_filename}' "
                "for conflict resolution"
            )

        logger.debug("Waiting for remote server cache to refresh...")
        await self._sleep(5)
        logger.warning(
            f"File '{filename}' already exists in {final_dir_path}, "
            f"renamed to '{new_filename}' to avoid conflict"
        )
        return new_filename

    async def resolve_before_rename(
        self,
        directory_path: str,
        current_filename: str,
        target_filename: str,
    ) -> str:
        existing_files = await self._client.list_files(directory_path)
        if existing_files is None:
            return target_filename

        existing_names = {f.name for f in existing_files if f.name != current_filename}
        if target_filename not in existing_names:
            return target_filename

        candidate = self._next_available_name(
            target_filename,
            existing_names,
        )
        logger.warning(
            f"File '{target_filename}' already exists in {directory_path}, "
            f"renaming to '{candidate}'"
        )
        return candidate

    def _next_available_name(
        self,
        filename: str,
        existing_names: set[str],
    ) -> str:
        stem, ext = os.path.splitext(filename)
        for i in range(1, self._MAX_CONFLICT_SUFFIX + 1):
            candidate = f"{stem} ({i}){ext}"
            if candidate not in existing_names:
                return candidate

        raise OpenListFileConflictError(
            f"Cannot resolve filename conflict: '{filename}' "
            f"(tried up to ({self._MAX_CONFLICT_SUFFIX}))"
        )
