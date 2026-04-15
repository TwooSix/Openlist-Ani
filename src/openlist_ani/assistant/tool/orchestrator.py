"""
Tool orchestrator — parallel/serial dispatch engine.

- partitionToolCalls: groups consecutive concurrency-safe tools for concurrent execution
- ToolOrchestrator: dispatches batches via asyncio or serially
- Tool result truncation: per-result and per-message aggregate budget
- Tool progress reporting: per-tool activity descriptions for UI spinners

The main entry point ``execute_tool_calls`` is an AsyncGenerator that
yields each ToolResult as it completes. This allows the caller
(AgenticLoop) to check for pending user messages between individual
tool completions and decide whether to interrupt remaining tools.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field

from openlist_ani.assistant._constants import (
    MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
    MAX_TOOL_USE_CONCURRENCY,
)
from openlist_ani.assistant.core.models import ToolCall, ToolResult

from .registry import ToolRegistry

from loguru import logger

# Type alias for progress callbacks.
# Called with (tool_name, activity_description_or_None) before each tool execution.
ProgressCallback = Callable[[str, str | None], None]


@dataclass
class Batch:
    """A batch of tool calls with uniform concurrency behavior."""

    is_concurrency_safe: bool
    tool_calls: list[ToolCall] = field(default_factory=list)


def partition_tool_calls(
    tool_calls: list[ToolCall],
    registry: ToolRegistry,
) -> list[Batch]:
    """Partition tool calls into batches for execution.

    Consecutive concurrency-safe tools merge into one batch
    for concurrent execution. Each non-safe tool gets its own
    batch for serial execution.

    The decision is **per-input**: the same tool may be safe for one
    input and serial for another (e.g., a bash tool could be safe for
    ``ls`` but not for ``rm``).

    Args:
        tool_calls: List of tool calls from the model.
        registry: Tool registry to look up is_concurrency_safe.

    Returns:
        Ordered list of Batch objects.
    """
    batches: list[Batch] = []
    for tc in tool_calls:
        tool = registry.get(tc.name)
        safe = tool.is_concurrency_safe(tc.arguments) if tool else False

        if safe and batches and batches[-1].is_concurrency_safe:
            # Merge into the current concurrency-safe batch
            batches[-1].tool_calls.append(tc)
        else:
            batches.append(Batch(is_concurrency_safe=safe, tool_calls=[tc]))

    return batches


def _truncate_result(result: ToolResult, max_chars: int) -> ToolResult:
    """Truncate a single tool result if it exceeds max_chars."""
    if len(result.content) <= max_chars:
        return result
    truncated = result.content[:max_chars]
    notice = (
        f"\n\n[... truncated: {len(result.content)} chars → {max_chars} chars. "
        f"Result was too large.]"
    )
    logger.info(
        f"Tool '{result.name}' result truncated: "
        f"{len(result.content)} → {max_chars} chars"
    )
    return ToolResult(
        tool_call_id=result.tool_call_id,
        name=result.name,
        content=truncated + notice,
        is_error=result.is_error,
    )


def apply_per_message_budget(
    results: list[ToolResult],
    per_result_max: int = MAX_TOOL_RESULT_CHARS,
    aggregate_max: int = MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
) -> list[ToolResult]:
    """Apply per-result and per-message aggregate truncation.

    1. Truncate individual results exceeding per_result_max.
    2. If aggregate chars still exceed aggregate_max, truncate the
       largest remaining results until under budget.
    """
    # Step 1: per-result truncation
    truncated = [_truncate_result(r, per_result_max) for r in results]

    # Step 2: aggregate budget
    total = sum(len(r.content) for r in truncated)
    if total <= aggregate_max:
        return truncated

    # Sort indices by content length (largest first) for greedy trimming
    by_size = sorted(
        range(len(truncated)), key=lambda i: len(truncated[i].content), reverse=True
    )
    for idx in by_size:
        if total <= aggregate_max:
            break
        r = truncated[idx]
        old_len = len(r.content)
        # Shrink this result so aggregate fits
        target_len = max(1000, old_len - (total - aggregate_max))
        truncated[idx] = _truncate_result(r, target_len)
        total -= old_len - len(truncated[idx].content)

    return truncated


class ToolOrchestrator:
    """Dispatches tool calls with parallel/serial scheduling.

    The main entry point ``execute_tool_calls`` is an AsyncGenerator
    that yields each ``ToolResult`` as it completes. This allows the
    caller (AgenticLoop) to check for pending user messages between
    individual tool completions.

    Supports an optional progress callback for UI integration:
    - Called before each tool execution with (tool_name, activity_description)
    - activity_description comes from tool.get_activity_description(input)
    - If None, the frontend can fall back to the tool name
    """

    def __init__(
        self,
        registry: ToolRegistry,
        max_concurrency: int = MAX_TOOL_USE_CONCURRENCY,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._registry = registry
        self._max_concurrency = max_concurrency
        self._on_progress = on_progress

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        """Set or clear the progress callback.

        Args:
            callback: Called with (tool_name, activity_description) before
                each tool execution, or None to clear.
        """
        self._on_progress = callback

    def _report_progress(self, tc: ToolCall) -> None:
        """Report tool execution progress via callback.

        Looks up the tool and calls get_activity_description(input).
        """
        if not self._on_progress:
            return
        tool = self._registry.get(tc.name)
        activity = None
        if tool:
            activity = tool.get_activity_description(tc.arguments)
        try:
            self._on_progress(tc.name, activity)
        except Exception as e:
            logger.debug(f"Progress callback error: {e}")

    async def execute_tool_calls(
        self, tool_calls: list[ToolCall]
    ) -> AsyncGenerator[ToolResult, None]:
        """Execute tool calls, yielding each result as it completes.

        Maintains batch logic (concurrent read / serial write),
        but yields results one-by-one within each batch.

        The caller is responsible for:
        - Collecting results for message assembly
        - Checking for pending user messages between yields
        - Applying per-message truncation budget on collected results
          (via ``apply_per_message_budget``)

        Args:
            tool_calls: Tool calls from the model response.

        Yields:
            ToolResult for each completed tool execution.
        """
        batches = partition_tool_calls(tool_calls, self._registry)

        for batch in batches:
            if batch.is_concurrency_safe:
                async for result in self._run_concurrent_stream(batch.tool_calls):
                    yield result
            else:
                async for result in self._run_serial_stream(batch.tool_calls):
                    yield result

    async def _run_serial_stream(
        self, tool_calls: list[ToolCall]
    ) -> AsyncGenerator[ToolResult, None]:
        """Run write tools serially, yielding each result."""
        for tc in tool_calls:
            self._report_progress(tc)
            yield await self._registry.dispatch(tc)

    async def _run_concurrent_stream(
        self, tool_calls: list[ToolCall]
    ) -> AsyncGenerator[ToolResult, None]:
        """Run read-only tools concurrently, yielding each as it completes.

        Uses asyncio.Task + asyncio.Queue so results are yielded in
        completion order (whichever finishes first). On generator close
        (e.g. caller breaks out), pending tasks are cancelled via the
        finally block.
        """
        semaphore = asyncio.Semaphore(self._max_concurrency)
        pending_tasks: set[asyncio.Task] = set()
        result_queue: asyncio.Queue[ToolResult | BaseException] = asyncio.Queue()

        async def _run_with_limit(tc: ToolCall) -> None:
            try:
                async with semaphore:
                    self._report_progress(tc)
                    result = await self._registry.dispatch(tc)
                    await result_queue.put(result)
            except Exception as exc:
                # Push the exception so the consumer sees it
                await result_queue.put(exc)

        try:
            # Start all tasks
            for tc in tool_calls:
                task = asyncio.create_task(_run_with_limit(tc))
                pending_tasks.add(task)
                task.add_done_callback(pending_tasks.discard)

            # Yield results as they complete
            completed = 0
            while completed < len(tool_calls):
                item = await result_queue.get()
                completed += 1
                if isinstance(item, BaseException):
                    # Re-raise tool execution error as a failed ToolResult
                    # so the caller can handle it uniformly
                    raise item
                yield item
        finally:
            # Cancel any still-running tasks on generator close
            for task in pending_tasks:
                if not task.done():
                    task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
