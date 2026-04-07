"""Tests for the core agentic loop."""

import pytest
from pathlib import Path

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import (
    AgenticLoop,
    _is_overloaded,
    _is_prompt_too_long,
    _is_transient,
)
from openlist_ani.assistant.core.models import (
    EventType,
    LoopEvent,
    ProviderResponse,
    Role,
    ToolCall,
)
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool, WriteTool


def _collect_text(events: list[LoopEvent]) -> str:
    """Extract final text from a list of LoopEvents."""
    for event in reversed(events):
        if event.type == EventType.TEXT_DONE:
            return event.text
    return ""


@pytest.fixture
def memory(tmp_path: Path):
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    return MemoryManager(data_dir=data_dir, project_root=project)


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(ReadOnlyTool("grep", "found matches"))
    reg.register(WriteTool("edit", "file edited"))
    return reg


class TestAgenticLoop:
    @pytest.mark.asyncio
    async def test_pure_text_response(self, memory, registry):
        """No tool calls -> yields events including TEXT_DONE."""
        provider = MockProvider([
            ProviderResponse(text="The answer is 42."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("What is the answer?"):
            results.append(event)

        text = _collect_text(results)
        assert text == "The answer is 42."
        # Should have THINKING and TEXT_DONE at minimum
        types = [e.type for e in results]
        assert EventType.THINKING in types
        assert EventType.TEXT_DONE in types

    @pytest.mark.asyncio
    async def test_single_tool_call_round(self, memory, registry):
        """One round of tool calls then text response."""
        provider = MockProvider([
            # Round 1: tool call
            ProviderResponse(
                text="",
                tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
            ),
            # Round 2: final text
            ProviderResponse(text="Found the results."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Search for something"):
            results.append(event)

        text = _collect_text(results)
        assert text == "Found the results."
        assert provider._call_count == 2

        # Should have tool events
        types = [e.type for e in results]
        assert EventType.TOOL_START in types
        assert EventType.TOOL_END in types

    @pytest.mark.asyncio
    async def test_multi_round_tool_calls(self, memory, registry):
        """Multiple rounds of tool calls before final text."""
        provider = MockProvider([
            ProviderResponse(
                tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
            ),
            ProviderResponse(
                tool_calls=[ToolCall(id="tc_2", name="edit", arguments={})],
            ),
            ProviderResponse(text="All done."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Do something complex"):
            results.append(event)

        text = _collect_text(results)
        assert text == "All done."
        assert provider._call_count == 3

    @pytest.mark.asyncio
    async def test_max_rounds_safety(self, memory, registry):
        """Infinite tool calls should be stopped at max rounds."""
        responses = [
            ProviderResponse(
                tool_calls=[ToolCall(id=f"tc_{i}", name="grep", arguments={})],
            )
            for i in range(20)
        ]
        provider = MockProvider(responses)
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory, max_rounds=3)

        results = []
        async for event in loop.process("Infinite loop"):
            results.append(event)

        text = _collect_text(results)
        assert "maximum tool call rounds" in text.lower()
        assert provider._call_count == 3

    @pytest.mark.asyncio
    async def test_tool_results_injected(self, memory, registry):
        """Tool results should be injected back into the conversation."""
        provider = MockProvider([
            ProviderResponse(
                tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
            ),
            ProviderResponse(text="Based on the grep results..."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Search files"):
            results.append(event)

        # Check that the second call received messages with tool results
        second_call_messages = provider._calls[1][0]
        tool_messages = [m for m in second_call_messages if m.role == Role.TOOL]
        assert len(tool_messages) >= 1
        assert tool_messages[-1].tool_results[0].content == "found matches"

    @pytest.mark.asyncio
    async def test_session_persistence(self, memory, registry):
        """Conversation turns should be persisted to session file."""
        provider = MockProvider([
            ProviderResponse(text="Hello there!"),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Hi"):
            results.append(event)

        history = await memory.load_session_history()
        assert "Hi" in history
        assert "Hello there!" in history

    @pytest.mark.asyncio
    async def test_session_persistence_with_tools(self, memory, registry):
        """Tool names should be recorded in session history."""
        provider = MockProvider([
            ProviderResponse(
                tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
            ),
            ProviderResponse(text="Found results."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Search for something"):
            results.append(event)

        history = await memory.load_session_history()
        assert "grep" in history


class TestErrorClassification:
    """Tests for error classification helpers."""

    def test_prompt_too_long_detection(self):
        """Should detect prompt-too-long errors from various providers."""
        assert _is_prompt_too_long(RuntimeError("prompt is too long"))
        assert _is_prompt_too_long(RuntimeError("maximum context length exceeded"))
        assert _is_prompt_too_long(RuntimeError("context_length_exceeded"))
        assert _is_prompt_too_long(RuntimeError("This request too large for model"))
        assert not _is_prompt_too_long(RuntimeError("random error"))
        assert not _is_prompt_too_long(RuntimeError(""))

    def test_overloaded_detection(self):
        """Should detect rate-limit and overloaded errors."""
        assert _is_overloaded(RuntimeError("rate_limit_exceeded"))
        assert _is_overloaded(RuntimeError("Rate limit reached"))
        assert _is_overloaded(RuntimeError("server overloaded"))
        assert _is_overloaded(RuntimeError("429 Too Many Requests"))
        assert not _is_overloaded(RuntimeError("random error"))

    def test_transient_detection(self):
        """Should detect transient errors worth retrying."""
        assert _is_transient(RuntimeError("connection reset"))
        assert _is_transient(RuntimeError("request timed out"))
        assert _is_transient(RuntimeError("500 internal server error"))
        assert _is_transient(RuntimeError("502 bad gateway"))
        assert _is_transient(RuntimeError("rate_limit"))  # also transient
        assert not _is_transient(RuntimeError("invalid api key"))
        assert not _is_transient(RuntimeError("permission denied"))


class TestErrorRecovery:
    """Tests for error recovery in the agentic loop."""

    @pytest.mark.asyncio
    async def test_transient_error_retry_succeeds(self, memory, registry):
        """Should retry on transient errors and succeed."""
        call_count = 0

        class TransientErrorProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("429 Too Many Requests")
                return ProviderResponse(text="Recovered!")

        provider = TransientErrorProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Hello"):
            results.append(event)

        text = _collect_text(results)
        assert text == "Recovered!"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_prompt_too_long_reactive_compact(self, memory, registry):
        """Should attempt reactive compact on prompt-too-long errors."""
        call_count = 0

        class PromptTooLongProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                nonlocal call_count
                call_count += 1
                # Call 1: main loop call → prompt too long
                # This triggers reactive compact via force_compact:
                # Call 2: force_compact's LLM summary call → return summary
                # Call 3: retry after compact → normal response
                if call_count == 1:
                    raise RuntimeError("prompt is too long for this model")
                if call_count == 2:
                    return ProviderResponse(
                        text="<summary>Conversation summary</summary>"
                    )
                return ProviderResponse(text="OK after compact!")

        provider = PromptTooLongProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(
            provider, registry, context, memory,
            max_context_chars=1_000_000,
        )

        results = []
        async for event in loop.process("Hello"):
            results.append(event)

        text = _collect_text(results)
        assert text == "OK after compact!"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_unrecoverable_error_graceful_message(self, memory, registry):
        """Should yield graceful error message on unrecoverable errors."""

        class AlwaysFailProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                raise RuntimeError("Invalid API key")

        provider = AlwaysFailProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Hello"):
            results.append(event)

        text = _collect_text(results)
        assert "error" in text.lower()
        assert "try again" in text.lower()

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_graceful(self, memory, registry):
        """Should yield graceful message when all retries are exhausted."""
        call_count = 0

        class PersistentErrorProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                nonlocal call_count
                call_count += 1
                raise RuntimeError("connection reset by peer")

        provider = PersistentErrorProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Hello"):
            results.append(event)

        text = _collect_text(results)
        assert "error" in text.lower()
        # Should have retried the maximum number of times
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_error_preserves_conversation_state(self, memory, registry):
        """After an error, the loop should still be usable for next messages."""
        call_count = 0

        class RecoverableProvider(MockProvider):
            async def chat_completion(self, messages, tools=None, max_tokens_override=None, temperature=None):
                nonlocal call_count
                call_count += 1
                if call_count <= 3:  # First turn: all 3 retries fail
                    raise RuntimeError("server error 500")
                return ProviderResponse(text="Working now!")

        provider = RecoverableProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        # First turn: error
        results1 = []
        async for event in loop.process("First"):
            results1.append(event)
        text1 = _collect_text(results1)
        assert "error" in text1.lower()

        # Second turn: should still work
        results2 = []
        async for event in loop.process("Second"):
            results2.append(event)
        text2 = _collect_text(results2)
        assert text2 == "Working now!"


class TestGeneratorCancellation:
    """Tests for process() generator cancellation (task cleanup / lock release)."""

    @pytest.mark.asyncio
    async def test_early_break_releases_lock(self, memory, registry):
        """Breaking out of process() early should cancel the task and release the lock.

        This tests the fix for the spinner-hang bug: if the consumer breaks
        out of the async-for (e.g. KeyboardInterrupt), the background _run()
        task must be cancelled so it releases self._lock; otherwise the next
        process() call deadlocks.
        """
        import asyncio

        provider = MockProvider([
            ProviderResponse(text="First response."),
            ProviderResponse(text="Second response."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        # First call: break out immediately after the first event
        async for event in loop.process("Hello"):
            break  # Simulates generator close (like KeyboardInterrupt)

        # Second call: should NOT deadlock — must complete within a timeout
        results = []
        try:
            async with asyncio.timeout(5):
                async for event in loop.process("Hello again"):
                    results.append(event)
        except asyncio.TimeoutError:
            pytest.fail("process() deadlocked — lock was not released after generator close")

        # Should have gotten a valid response
        text = _collect_text(results)
        assert text  # Non-empty response

    @pytest.mark.asyncio
    async def test_multiple_early_breaks(self, memory, registry):
        """Multiple consecutive early breaks should not corrupt state."""
        import asyncio

        provider = MockProvider([
            ProviderResponse(text="Response 1."),
            ProviderResponse(text="Response 2."),
            ProviderResponse(text="Response 3."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        # Break out twice
        async for event in loop.process("First"):
            break
        async for event in loop.process("Second"):
            break

        # Third call should still work
        results = []
        try:
            async with asyncio.timeout(5):
                async for event in loop.process("Third"):
                    results.append(event)
        except asyncio.TimeoutError:
            pytest.fail("process() deadlocked after multiple early breaks")

        text = _collect_text(results)
        assert text
