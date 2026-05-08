"""OpenList file rename adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from openlist_ani.application.anime_library_ingestion.models import PipelineContext
from openlist_ani.domain.download_task.file_renamer import (
    FileRenameError,
    RenamedFile,
    RenameRequest,
)
from openlist_ani.integrations.openlist import (
    OpenListClient,
    OpenListFileConflictError,
    OpenListFileConflictResolver,
)
from openlist_ani.logger import logger


class OpenListFileRenamer:
    """Adapter for renaming files already materialized in OpenList storage."""

    def __init__(
        self,
        client: OpenListClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._sleep = sleep

    async def rename(self, context: PipelineContext[RenameRequest]) -> RenamedFile:
        request = context.payload
        if request.target_filename == request.source_filename:
            logger.debug(
                f"Rename skipped for {context.workflow_id}: "
                f"source already matches target ({request.source_filename})"
            )
            return RenamedFile(
                release=request.release,
                directory_path=request.directory_path,
                filename=request.source_filename,
            )

        conflict_resolver = OpenListFileConflictResolver(self._client, self._sleep)
        try:
            target_filename = await conflict_resolver.resolve_before_rename(
                request.directory_path,
                request.source_filename,
                request.target_filename,
            )
        except OpenListFileConflictError as e:
            raise FileRenameError(str(e)) from e

        source_path = f"{request.directory_path.rstrip('/')}/{request.source_filename}"
        logger.debug(
            f"OpenList rename: task={context.workflow_id}, "
            f"source={request.source_filename}, target={target_filename}"
        )
        if not await self._client.rename_file(source_path, target_filename):
            raise FileRenameError(
                f"Failed to rename '{request.source_filename}' to '{target_filename}'"
            )
        await self._sleep(5)

        return RenamedFile(
            release=request.release,
            directory_path=request.directory_path,
            filename=target_filename,
        )
