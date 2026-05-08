from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")


class PipelineBuffer(Generic[T]):
    def __init__(self, name: str, maxsize: int = 0) -> None:
        self.name = name
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: T) -> None:
        await self._queue.put(item)

    async def get(self) -> T:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()

    async def join(self) -> None:
        await self._queue.join()
