from __future__ import annotations

import asyncio
import traceback
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from openlist_ani.application.anime_library_ingestion.ports import EventPublisherPort
from openlist_ani.application.common import OAniEvent, OAniEventType, Severity
from openlist_ani.logger import logger, sanitize_for_log

from .buffer import PipelineBuffer

ItemT = TypeVar("ItemT")


class PipelineStage(ABC, Generic[ItemT]):
    def __init__(
        self,
        name: str,
        input_buffer: PipelineBuffer[ItemT] | None,
        event_publisher: EventPublisherPort,
        worker_count: int = 1,
    ) -> None:
        self.name = name
        self.input_buffer = input_buffer
        self.event_publisher = event_publisher
        self.worker_count = max(1, worker_count)
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.debug(f"Pipeline stage started: {self.name}")
        while self._running:
            if self.input_buffer is None:
                await self.process_batch()
                continue

            item = await self.input_buffer.get()
            try:
                await self.process_item(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self.on_error(e, item)
            finally:
                self.input_buffer.task_done()

    async def stop(self) -> None:
        self._running = False

    async def process_batch(self) -> None:
        await asyncio.sleep(0)

    @abstractmethod
    async def process_item(self, item: ItemT) -> None:
        raise NotImplementedError

    async def on_error(self, error: Exception, item: ItemT | None = None) -> None:
        item_summary = _summarize_item(item)
        logger.error(
            f"Pipeline item failed in stage={self.name}; "
            f"pipeline will continue; item={item_summary}; error={error}"
        )
        trace = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        logger.debug(f"Pipeline item failure traceback: {sanitize_for_log(trace)}")
        await self.event_publisher.publish(
            OAniEvent(
                event_type=OAniEventType.PIPELINE_ERROR,
                severity=Severity.ERROR,
                source=self.name,
                payload={
                    "error": sanitize_for_log(error),
                    "item": item_summary,
                },
            )
        )


def _summarize_item(item: object | None) -> dict[str, object] | None:
    if item is None:
        return None

    summary: dict[str, object] = {}
    workflow_id = getattr(item, "workflow_id", None)
    if workflow_id is not None:
        summary["workflow_id"] = workflow_id

    payload = getattr(item, "payload", item)
    release = getattr(payload, "release", None)
    if release is not None:
        summary["title"] = getattr(release, "title", None)
        summary["anime_name"] = getattr(release, "anime_name", None)
        summary["season"] = getattr(release, "season", None)
        summary["episode"] = getattr(release, "episode", None)

    state = getattr(payload, "state", None)
    if state is not None:
        summary["state"] = str(state)

    return summary or {"type": type(item).__name__}
