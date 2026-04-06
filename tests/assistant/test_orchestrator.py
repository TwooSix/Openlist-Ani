"""Tests for parallel/serial tool orchestration."""


import pytest

from openlist_ani.assistant.core.models import ToolCall
from openlist_ani.assistant.tool.orchestrator import (
    ToolOrchestrator,
    partition_tool_calls,
)
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import ReadOnlyTool, WriteTool


class TestPartitionToolCalls:
    def _make_registry(self, *tools):
        registry = ToolRegistry()
        for t in tools:
            registry.register(t)
        return registry

    def test_all_concurrency_safe_merge(self):
        """Consecutive concurrency-safe tools should merge into one batch."""
        registry = self._make_registry(
            ReadOnlyTool("a"), ReadOnlyTool("b"), ReadOnlyTool("c")
        )
        calls = [
            ToolCall(id="1", name="a", arguments={}),
            ToolCall(id="2", name="b", arguments={}),
            ToolCall(id="3", name="c", arguments={}),
        ]
        batches = partition_tool_calls(calls, registry)

        assert len(batches) == 1
        assert batches[0].is_concurrency_safe is True
        assert len(batches[0].tool_calls) == 3

    def test_all_write_separate(self):
        """Each non-concurrency-safe tool should get its own batch."""
        registry = self._make_registry(WriteTool("x"), WriteTool("y"))
        calls = [
            ToolCall(id="1", name="x", arguments={}),
            ToolCall(id="2", name="y", arguments={}),
        ]
        batches = partition_tool_calls(calls, registry)

        assert len(batches) == 2
        for b in batches:
            assert b.is_concurrency_safe is False
            assert len(b.tool_calls) == 1

    def test_mixed_safe_unsafe_safe(self):
        """[safe, safe, unsafe, safe, safe] -> 3 batches."""
        registry = self._make_registry(
            ReadOnlyTool("r1"), ReadOnlyTool("r2"),
            WriteTool("w1"),
            ReadOnlyTool("r3"), ReadOnlyTool("r4"),
        )
        calls = [
            ToolCall(id="1", name="r1", arguments={}),
            ToolCall(id="2", name="r2", arguments={}),
            ToolCall(id="3", name="w1", arguments={}),
            ToolCall(id="4", name="r3", arguments={}),
            ToolCall(id="5", name="r4", arguments={}),
        ]
        batches = partition_tool_calls(calls, registry)

        assert len(batches) == 3
        assert batches[0].is_concurrency_safe is True
        assert len(batches[0].tool_calls) == 2
        assert batches[1].is_concurrency_safe is False
        assert len(batches[1].tool_calls) == 1
        assert batches[2].is_concurrency_safe is True
        assert len(batches[2].tool_calls) == 2

    def test_unknown_tool_treated_as_unsafe(self):
        """Unknown tools default to is_concurrency_safe=False."""
        registry = ToolRegistry()
        calls = [ToolCall(id="1", name="unknown", arguments={})]
        batches = partition_tool_calls(calls, registry)

        assert len(batches) == 1
        assert batches[0].is_concurrency_safe is False

    def test_empty_list(self):
        """Empty tool call list produces no batches."""
        registry = ToolRegistry()
        batches = partition_tool_calls([], registry)
        assert batches == []


class TestToolOrchestrator:
    @pytest.mark.asyncio
    async def test_concurrent_read_only(self):
        """Read-only tools should run concurrently."""
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("a", "result_a"))
        registry.register(ReadOnlyTool("b", "result_b"))

        orchestrator = ToolOrchestrator(registry)
        calls = [
            ToolCall(id="1", name="a", arguments={}),
            ToolCall(id="2", name="b", arguments={}),
        ]
        results = await orchestrator.execute_tool_calls(calls)

        assert len(results) == 2
        assert results[0].content == "result_a"
        assert results[1].content == "result_b"

    @pytest.mark.asyncio
    async def test_serial_write(self):
        """Write tools should run serially."""
        registry = ToolRegistry()
        w1 = WriteTool("w1", "done_1")
        w2 = WriteTool("w2", "done_2")
        registry.register(w1)
        registry.register(w2)

        orchestrator = ToolOrchestrator(registry)
        calls = [
            ToolCall(id="1", name="w1", arguments={}),
            ToolCall(id="2", name="w2", arguments={}),
        ]
        results = await orchestrator.execute_tool_calls(calls)

        assert len(results) == 2
        assert results[0].content == "done_1"
        assert results[1].content == "done_2"
        assert w1.call_count == 1
        assert w2.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_batches_order_preserved(self):
        """Results from mixed batches should maintain order."""
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("r1", "r1_result"))
        registry.register(WriteTool("w1", "w1_result"))
        registry.register(ReadOnlyTool("r2", "r2_result"))

        orchestrator = ToolOrchestrator(registry)
        calls = [
            ToolCall(id="1", name="r1", arguments={}),
            ToolCall(id="2", name="w1", arguments={}),
            ToolCall(id="3", name="r2", arguments={}),
        ]
        results = await orchestrator.execute_tool_calls(calls)

        assert len(results) == 3
        assert results[0].content == "r1_result"
        assert results[1].content == "w1_result"
        assert results[2].content == "r2_result"

    @pytest.mark.asyncio
    async def test_empty_tool_calls(self):
        """Empty list should return empty results."""
        registry = ToolRegistry()
        orchestrator = ToolOrchestrator(registry)
        results = await orchestrator.execute_tool_calls([])
        assert results == []

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """Semaphore should limit concurrent executions."""
        registry = ToolRegistry()
        # Create many read-only tools
        for i in range(20):
            registry.register(ReadOnlyTool(f"r{i}", f"result_{i}"))

        orchestrator = ToolOrchestrator(registry, max_concurrency=5)
        calls = [ToolCall(id=str(i), name=f"r{i}", arguments={}) for i in range(20)]
        results = await orchestrator.execute_tool_calls(calls)

        assert len(results) == 20
        # All results should be present (order preserved)
        for i in range(20):
            assert results[i].content == f"result_{i}"
