"""Tests for tool result truncation and autocompact."""

import pytest

from openlist_ani.assistant._constants import (
    MAX_TOOL_RESULT_CHARS,
)
from openlist_ani.assistant.core.models import (
    Message,
    ProviderResponse,
    Role,
    ToolCall,
    ToolResult,
)
from openlist_ani.assistant.memory.compactor import (
    AutoCompactor,
    ReadFileTracker,
    _build_post_compact_summary_message,
    _format_compact_summary,
    get_autocompact_threshold,
)
from openlist_ani.assistant.tool.orchestrator import (
    ToolOrchestrator,
    _apply_per_message_budget,
    _truncate_result,
)
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool


class TestToolResultTruncation:
    def test_truncate_result_under_limit(self):
        """Results under the limit should not be modified."""
        result = ToolResult(
            tool_call_id="1", name="test", content="short content"
        )
        truncated = _truncate_result(result, 1000)
        assert truncated.content == "short content"

    def test_truncate_result_over_limit(self):
        """Results over the limit should be truncated with a notice."""
        long_content = "x" * 100
        result = ToolResult(
            tool_call_id="1", name="test", content=long_content
        )
        truncated = _truncate_result(result, 50)
        assert len(truncated.content) < len(long_content) + 100
        assert "truncated" in truncated.content.lower()
        assert "100 chars" in truncated.content

    def test_truncate_preserves_metadata(self):
        """Truncation should preserve tool_call_id, name, is_error."""
        result = ToolResult(
            tool_call_id="abc", name="my_tool", content="x" * 200, is_error=True
        )
        truncated = _truncate_result(result, 50)
        assert truncated.tool_call_id == "abc"
        assert truncated.name == "my_tool"
        assert truncated.is_error is True


class TestPerMessageBudget:
    def test_all_under_budget(self):
        """No truncation when all results are small."""
        results = [
            ToolResult(tool_call_id=str(i), name=f"t{i}", content="small")
            for i in range(5)
        ]
        truncated = _apply_per_message_budget(results)
        assert all(r.content == "small" for r in truncated)

    def test_per_result_truncation(self):
        """Individual results exceeding per_result_max are truncated."""
        results = [
            ToolResult(
                tool_call_id="1", name="big",
                content="x" * 200,
            ),
            ToolResult(
                tool_call_id="2", name="small",
                content="ok",
            ),
        ]
        truncated = _apply_per_message_budget(
            results, per_result_max=100, aggregate_max=10_000
        )
        assert "truncated" in truncated[0].content.lower()
        assert truncated[1].content == "ok"

    def test_aggregate_budget_trims_largest(self):
        """When aggregate exceeds budget, largest results are trimmed."""
        results = [
            ToolResult(
                tool_call_id="1", name="big",
                content="x" * 5000,
            ),
            ToolResult(
                tool_call_id="2", name="medium",
                content="y" * 3000,
            ),
            ToolResult(
                tool_call_id="3", name="small",
                content="z" * 100,
            ),
        ]
        # Total aggregate is 8100 chars, budget is 5000
        truncated = _apply_per_message_budget(
            results, per_result_max=10_000, aggregate_max=5000
        )
        total = sum(len(r.content) for r in truncated)
        assert total <= 5500  # Should be roughly within budget (notice adds chars)

    def test_empty_results(self):
        """Empty results list returns empty."""
        assert _apply_per_message_budget([]) == []


class TestOrchestratorTruncation:
    @pytest.mark.asyncio
    async def test_large_result_truncated_by_orchestrator(self):
        """Orchestrator should truncate tool results that exceed the budget."""
        # Create a tool that returns a large result
        large_tool = ReadOnlyTool("big_tool", "x" * (MAX_TOOL_RESULT_CHARS + 10_000))
        registry = ToolRegistry()
        registry.register(large_tool)

        orchestrator = ToolOrchestrator(registry)
        calls = [ToolCall(id="1", name="big_tool", arguments={})]
        results = await orchestrator.execute_tool_calls(calls)

        assert len(results) == 1
        # Should be truncated
        assert len(results[0].content) < MAX_TOOL_RESULT_CHARS + 200
        assert "truncated" in results[0].content.lower()


class TestFormatCompactSummary:
    def test_strips_analysis_keeps_summary(self):
        """Should strip <analysis> and format <summary>."""
        raw = (
            "<analysis>My thinking here...</analysis>\n\n"
            "<summary>1. Primary Request\n2. Key Concepts</summary>"
        )
        result = _format_compact_summary(raw)
        assert "My thinking here" not in result
        assert "Primary Request" in result
        assert "Summary:" in result

    def test_no_tags(self):
        """Should handle text without tags."""
        raw = "Just plain text summary."
        result = _format_compact_summary(raw)
        assert result == "Just plain text summary."

    def test_analysis_only(self):
        """Should strip analysis even without summary tags."""
        raw = "<analysis>Thinking...</analysis>\n\nPlain summary here."
        result = _format_compact_summary(raw)
        assert "Thinking" not in result
        assert "Plain summary here" in result


class TestBuildPostCompactSummaryMessage:
    def test_contains_continuation_instruction(self):
        """Post-compact message should instruct model to continue."""
        msg = _build_post_compact_summary_message("1. Primary Request: do X")
        assert "continued from a previous conversation" in msg
        assert "Resume directly" in msg
        assert "Primary Request" in msg


class TestAutocompactThreshold:
    def test_threshold_calculation(self):
        """Threshold should be contextWindow - reservedOutput - buffer."""
        # 512_000 chars context, reserved output = 20_000×4=80_000, buffer = 13_000×4=52_000
        threshold = get_autocompact_threshold(512_000)
        assert threshold == 512_000 - 80_000 - 52_000
        assert threshold == 380_000

    def test_small_context_window(self):
        """Should work with small context windows."""
        threshold = get_autocompact_threshold(200_000)
        # Expect: 200K context minus 80K reserved output minus 52K buffer equals 68K
        assert threshold == 68_000


class TestAutoCompactor:
    @pytest.mark.asyncio
    async def test_no_compact_under_threshold(self):
        """Should not compact when messages are under threshold."""
        provider = MockProvider()
        compactor = AutoCompactor(provider, max_context_chars=512_000)

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there!"),
        ]
        result = await compactor.maybe_compact(messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_compact_over_threshold(self):
        """Should compact when messages exceed threshold."""
        provider = MockProvider([
            ProviderResponse(
                text="<analysis>Thinking</analysis><summary>1. Primary: test\n2. Concepts: none</summary>"
            ),
        ])
        # Use a very small threshold to trigger compaction
        compactor = AutoCompactor(provider, max_context_chars=1000)

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="x" * 2000),
            Message(role=Role.ASSISTANT, content="y" * 2000),
        ]
        result = await compactor.maybe_compact(messages)

        assert result is not None
        assert len(result) == 2  # system + summary user msg
        assert result[0].role == Role.SYSTEM
        assert result[1].role == Role.USER
        assert "continued from a previous conversation" in result[1].content
        assert "Primary: test" in result[1].content

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        """Should stop after MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES."""

        class FailingProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                raise RuntimeError("Provider error")

        provider = FailingProvider()
        compactor = AutoCompactor(provider, max_context_chars=1000)

        messages = [
            Message(role=Role.SYSTEM, content="s"),
            Message(role=Role.USER, content="x" * 5000),
        ]

        # Should fail 3 times, then circuit breaker activates
        for _ in range(3):
            result = await compactor.maybe_compact(messages)
            assert result is None

        # Now circuit breaker should prevent even trying
        result = await compactor.maybe_compact(messages)
        assert result is None
        assert compactor._consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_system_message_preserved(self):
        """System message should be preserved after compaction."""
        provider = MockProvider([
            ProviderResponse(text="<summary>Summary here</summary>"),
        ])
        compactor = AutoCompactor(provider, max_context_chars=500)

        system_content = "I am the system prompt with rules."
        messages = [
            Message(role=Role.SYSTEM, content=system_content),
            Message(role=Role.USER, content="x" * 2000),
        ]
        result = await compactor.maybe_compact(messages)

        assert result is not None
        assert result[0].content == system_content


class TestForceCompact:
    """Tests for AutoCompactor.force_compact() (reactive compaction)."""

    @pytest.mark.asyncio
    async def test_force_compact_bypasses_threshold(self):
        """force_compact should work even when under threshold."""
        provider = MockProvider([
            ProviderResponse(
                text="<summary>Forced summary</summary>"
            ),
        ])
        # Large threshold = would never trigger automatically
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there!"),
        ]
        result = await compactor.force_compact(messages)

        assert result is not None
        assert len(result) == 2  # system + summary
        assert result[0].role == Role.SYSTEM
        assert "continued from a previous conversation" in result[1].content

    @pytest.mark.asyncio
    async def test_force_compact_returns_none_on_failure(self):
        """force_compact should return None if LLM call fails."""

        class FailingProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                raise RuntimeError("Provider down")

        provider = FailingProvider()
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)

        messages = [
            Message(role=Role.SYSTEM, content="s"),
            Message(role=Role.USER, content="x" * 100),
        ]
        result = await compactor.force_compact(messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_force_compact_resets_failure_count(self):
        """Successful force_compact should reset failure counter."""
        provider = MockProvider([
            ProviderResponse(text="<summary>Summary</summary>"),
        ])
        compactor = AutoCompactor(provider, max_context_chars=1_000_000)
        compactor._consecutive_failures = 2  # Simulate prior failures

        messages = [
            Message(role=Role.SYSTEM, content="s"),
            Message(role=Role.USER, content="x" * 100),
        ]
        result = await compactor.force_compact(messages)
        assert result is not None
        assert compactor._consecutive_failures == 0


class TestReadFileTracker:
    """Tests for ReadFileTracker."""

    def test_track_and_get_recent(self):
        """Should track files and return most recent first."""
        tracker = ReadFileTracker()
        tracker.track("/a.py", "content a")
        tracker.track("/b.py", "content b")
        tracker.track("/c.py", "content c")

        files = tracker.get_recent_files()
        assert len(files) == 3
        # Most recent first
        assert files[0][0] == "/c.py"
        assert files[1][0] == "/b.py"
        assert files[2][0] == "/a.py"

    def test_max_files_limit(self):
        """Should return at most max_files."""
        tracker = ReadFileTracker()
        for i in range(20):
            tracker.track(f"/file_{i}.py", f"content {i}")

        files = tracker.get_recent_files(max_files=3)
        assert len(files) == 3

    def test_per_file_truncation(self):
        """Large files should be truncated to max_tokens_per_file."""
        tracker = ReadFileTracker()
        large_content = "x" * 100_000
        tracker.track("/large.py", large_content)

        files = tracker.get_recent_files(max_tokens_per_file=1_000)
        assert len(files) == 1
        path, content = files[0]
        assert path == "/large.py"
        # 1000 tokens × 4 chars = 4000 chars + truncation notice
        assert len(content) < 5000
        assert "truncated" in content.lower()

    def test_total_budget(self):
        """Total files should fit within token budget."""
        tracker = ReadFileTracker()
        for i in range(10):
            tracker.track(f"/file_{i}.py", "x" * 10_000)

        # 500 tokens × 4 = 2000 chars budget — should fit only 1 file
        files = tracker.get_recent_files(
            total_token_budget=500, max_tokens_per_file=50_000
        )
        # With 10K content each, only a few should fit in 2000 chars
        assert len(files) <= 1

    def test_clear(self):
        """Clear should remove all tracked files."""
        tracker = ReadFileTracker()
        tracker.track("/a.py", "content")
        assert len(tracker.get_recent_files()) == 1

        tracker.clear()
        assert len(tracker.get_recent_files()) == 0

    def test_empty_tracker(self):
        """Empty tracker returns empty list."""
        tracker = ReadFileTracker()
        assert tracker.get_recent_files() == []

    def test_duplicate_path_updates(self):
        """Tracking the same path again should update content and timestamp."""
        tracker = ReadFileTracker()
        tracker.track("/a.py", "old content")
        tracker.track("/b.py", "content b")
        tracker.track("/a.py", "new content")  # Update

        files = tracker.get_recent_files()
        assert len(files) == 2
        # /a.py should be most recent now
        assert files[0][0] == "/a.py"
        assert files[0][1] == "new content"


class TestPostCompactFileRestoration:
    """Tests for file restoration after compaction."""

    @pytest.mark.asyncio
    async def test_compaction_with_file_tracker(self):
        """After compaction, tracked files should be re-injected."""
        provider = MockProvider([
            ProviderResponse(
                text="<summary>Conversation summary</summary>"
            ),
        ])
        tracker = ReadFileTracker()
        tracker.track("/src/main.py", "def main():\n    pass")
        tracker.track("/src/utils.py", "def helper():\n    return 42")

        compactor = AutoCompactor(
            provider, max_context_chars=500, file_tracker=tracker
        )

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="x" * 2000),
        ]
        result = await compactor.maybe_compact(messages)

        assert result is not None
        # Should have: system + summary + file restoration
        assert len(result) == 3
        assert result[0].role == Role.SYSTEM
        assert result[1].role == Role.USER
        assert "continued from a previous conversation" in result[1].content
        # File restoration message
        assert result[2].role == Role.USER
        assert "main.py" in result[2].content
        assert "utils.py" in result[2].content
        assert "def main():" in result[2].content

    @pytest.mark.asyncio
    async def test_compaction_without_file_tracker(self):
        """Without file tracker, compaction should work as before."""
        provider = MockProvider([
            ProviderResponse(text="<summary>Summary</summary>"),
        ])
        compactor = AutoCompactor(provider, max_context_chars=500)

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="x" * 2000),
        ]
        result = await compactor.maybe_compact(messages)

        assert result is not None
        assert len(result) == 2  # system + summary only

    @pytest.mark.asyncio
    async def test_compaction_with_empty_tracker(self):
        """With empty file tracker, no file restoration message."""
        provider = MockProvider([
            ProviderResponse(text="<summary>Summary</summary>"),
        ])
        tracker = ReadFileTracker()  # Empty

        compactor = AutoCompactor(
            provider, max_context_chars=500, file_tracker=tracker
        )

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(role=Role.USER, content="x" * 2000),
        ]
        result = await compactor.maybe_compact(messages)

        assert result is not None
        assert len(result) == 2  # system + summary only, no file message
