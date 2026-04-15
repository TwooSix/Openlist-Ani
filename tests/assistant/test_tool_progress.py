"""Tests for tool progress reporting system."""

import pytest

from openlist_ani.assistant.core.models import ToolCall
from openlist_ani.assistant.tool.orchestrator import ToolOrchestrator
from openlist_ani.assistant.tool.registry import ToolRegistry
from openlist_ani.assistant.tool.base import BaseTool


async def _collect_results(orchestrator, calls):
    """Helper to collect all results from the AsyncGenerator."""
    results = []
    async for result in orchestrator.execute_tool_calls(calls):
        results.append(result)
    return results


class ProgressTrackingTool(BaseTool):
    """A tool that reports activity descriptions."""

    def __init__(self, name: str = "grep", is_safe: bool = True) -> None:
        self._name = name
        self._is_safe = is_safe

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Test tool ({self._name})"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
            },
        }

    def is_concurrency_safe(self, tool_input=None) -> bool:
        return self._is_safe

    def get_activity_description(self, tool_input=None) -> str | None:
        if tool_input and "pattern" in tool_input:
            return f"Searching for {tool_input['pattern']}"
        return "Searching"

    async def execute(self, **kwargs) -> str:
        return "found results"


class NoDescriptionTool(BaseTool):
    """A tool without activity description."""

    @property
    def name(self) -> str:
        return "silent_tool"

    @property
    def description(self) -> str:
        return "A silent tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        return "done"


class TestToolProgressReporting:
    """Tests for tool progress/activity reporting via orchestrator."""

    @pytest.mark.asyncio
    async def test_progress_callback_called(self):
        """Progress callback should be called before each tool execution."""
        registry = ToolRegistry()
        registry.register(ProgressTrackingTool("grep"))

        progress_calls: list[tuple[str, str | None]] = []

        def on_progress(tool_name: str, activity: str | None) -> None:
            progress_calls.append((tool_name, activity))

        orchestrator = ToolOrchestrator(registry, on_progress=on_progress)

        tc = ToolCall(id="tc_1", name="grep", arguments={"pattern": "error"})
        await _collect_results(orchestrator, [tc])

        assert len(progress_calls) == 1
        assert progress_calls[0] == ("grep", "Searching for error")

    @pytest.mark.asyncio
    async def test_progress_with_no_description(self):
        """Should pass None for activity when tool has no description."""
        registry = ToolRegistry()
        registry.register(NoDescriptionTool())

        progress_calls: list[tuple[str, str | None]] = []

        def on_progress(tool_name: str, activity: str | None) -> None:
            progress_calls.append((tool_name, activity))

        orchestrator = ToolOrchestrator(registry, on_progress=on_progress)

        tc = ToolCall(id="tc_1", name="silent_tool", arguments={})
        await _collect_results(orchestrator, [tc])

        assert len(progress_calls) == 1
        assert progress_calls[0] == ("silent_tool", None)

    @pytest.mark.asyncio
    async def test_progress_for_concurrent_tools(self):
        """Should report progress for each tool in a concurrent batch."""
        registry = ToolRegistry()
        registry.register(ProgressTrackingTool("grep1"))
        registry.register(ProgressTrackingTool("grep2"))

        progress_calls: list[tuple[str, str | None]] = []

        def on_progress(tool_name: str, activity: str | None) -> None:
            progress_calls.append((tool_name, activity))

        orchestrator = ToolOrchestrator(registry, on_progress=on_progress)

        tool_calls = [
            ToolCall(id="tc_1", name="grep1", arguments={"pattern": "foo"}),
            ToolCall(id="tc_2", name="grep2", arguments={"pattern": "bar"}),
        ]
        await _collect_results(orchestrator, tool_calls)

        assert len(progress_calls) == 2
        names = {name for name, _ in progress_calls}
        assert names == {"grep1", "grep2"}

    @pytest.mark.asyncio
    async def test_progress_for_serial_tools(self):
        """Should report progress for each tool in a serial batch."""
        registry = ToolRegistry()
        write_tool = ProgressTrackingTool("edit", is_safe=False)
        registry.register(write_tool)

        progress_calls: list[tuple[str, str | None]] = []

        def on_progress(tool_name: str, activity: str | None) -> None:
            progress_calls.append((tool_name, activity))

        orchestrator = ToolOrchestrator(registry, on_progress=on_progress)

        tool_calls = [
            ToolCall(id="tc_1", name="edit", arguments={}),
            ToolCall(id="tc_2", name="edit", arguments={"pattern": "test"}),
        ]
        await _collect_results(orchestrator, tool_calls)

        assert len(progress_calls) == 2
        # First call has no pattern → generic description
        assert progress_calls[0] == ("edit", "Searching")
        # Second call has pattern → specific description
        assert progress_calls[1] == ("edit", "Searching for test")

    @pytest.mark.asyncio
    async def test_no_progress_callback(self):
        """Should work fine without a progress callback."""
        registry = ToolRegistry()
        registry.register(ProgressTrackingTool("grep"))

        orchestrator = ToolOrchestrator(registry)  # No callback

        tc = ToolCall(id="tc_1", name="grep", arguments={})
        results = await _collect_results(orchestrator, [tc])

        assert len(results) == 1
        assert results[0].content == "found results"

    @pytest.mark.asyncio
    async def test_set_progress_callback(self):
        """set_progress_callback should update the callback."""
        registry = ToolRegistry()
        registry.register(ProgressTrackingTool("grep"))

        calls1: list[str] = []
        calls2: list[str] = []

        def cb1(name: str, _: str | None) -> None:
            calls1.append(name)

        def cb2(name: str, _: str | None) -> None:
            calls2.append(name)

        orchestrator = ToolOrchestrator(registry, on_progress=cb1)

        tc = ToolCall(id="tc_1", name="grep", arguments={})
        await _collect_results(orchestrator, [tc])
        assert len(calls1) == 1

        # Change callback
        orchestrator.set_progress_callback(cb2)
        await _collect_results(orchestrator, [tc])
        assert len(calls1) == 1  # Not called again
        assert len(calls2) == 1

    @pytest.mark.asyncio
    async def test_progress_callback_error_handled(self):
        """Errors in progress callback should be silently caught."""
        registry = ToolRegistry()
        registry.register(ProgressTrackingTool("grep"))

        def bad_callback(name: str, activity: str | None) -> None:
            raise RuntimeError("callback error")

        orchestrator = ToolOrchestrator(registry, on_progress=bad_callback)

        tc = ToolCall(id="tc_1", name="grep", arguments={})
        # Should not raise despite callback error
        results = await _collect_results(orchestrator, [tc])
        assert len(results) == 1


class TestGetActivityDescription:
    """Tests for BaseTool.get_activity_description()."""

    def test_default_returns_none(self):
        """Default implementation should return None."""

        class MinimalTool(BaseTool):
            @property
            def name(self):
                return "t"

            @property
            def description(self):
                return "t"

            @property
            def parameters(self):
                return {}

            async def execute(self, **kwargs):
                return ""

        tool = MinimalTool()
        assert tool.get_activity_description() is None
        assert tool.get_activity_description({"key": "val"}) is None

    def test_custom_activity_description(self):
        """Custom tools can provide meaningful activity descriptions."""
        tool = ProgressTrackingTool("grep")
        assert tool.get_activity_description(None) == "Searching"
        assert tool.get_activity_description({"pattern": "TODO"}) == "Searching for TODO"
