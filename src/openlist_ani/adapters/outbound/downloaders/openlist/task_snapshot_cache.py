from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from openlist_ani.integrations.openlist import OpenListClient, OpenlistTask

TaskListFetcher = Callable[[], Awaitable[list[OpenlistTask] | None]]


class OpenListTaskSnapshotCache:
    """Very short-lived OpenList task-list snapshots shared by workflows."""

    def __init__(
        self,
        client: OpenListClient,
        ttl_seconds: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl_seconds = max(0.0, ttl_seconds)
        self._monotonic = monotonic
        self._items: dict[str, tuple[float, list[OpenlistTask]]] = {}
        self._generation = 0

    async def get_offline_download_undone(self) -> list[OpenlistTask] | None:
        return await self._get(
            "offline_download_undone",
            self._client.get_offline_download_undone,
        )

    async def get_offline_download_done(self) -> list[OpenlistTask] | None:
        return await self._get(
            "offline_download_done",
            self._client.get_offline_download_done,
        )

    async def get_offline_download_transfer_undone(
        self,
    ) -> list[OpenlistTask] | None:
        return await self._get(
            "offline_download_transfer_undone",
            self._client.get_offline_download_transfer_undone,
        )

    async def get_offline_download_transfer_done(self) -> list[OpenlistTask] | None:
        return await self._get(
            "offline_download_transfer_done",
            self._client.get_offline_download_transfer_done,
        )

    def invalidate(self) -> None:
        self._items.clear()
        self._generation += 1

    async def _get(
        self,
        key: str,
        fetcher: TaskListFetcher,
    ) -> list[OpenlistTask] | None:
        now = self._monotonic()
        cached = self._items.get(key)
        if cached is not None:
            expires_at, value = cached
            if now < expires_at:
                return value

        generation = self._generation
        value = await fetcher()
        if value is not None and generation == self._generation:
            self._items[key] = (now + self._ttl_seconds, value)
        return value
