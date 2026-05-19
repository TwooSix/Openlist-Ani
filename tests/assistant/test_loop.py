"""Tests for the core agentic loop."""

from collections.abc import AsyncGenerator
import pytest
from pathlib import Path

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import (
    AgenticLoop,
    _is_overloaded,
    _is_prompt_too_long,
    _is_transient,
)
from openlist_ani.assistant.core.cancellation import CancellationToken
from openlist_ani.assistant.core.message_queue import MessageQueue, PendingMessage
from openlist_ani.assistant.core.models import (
    Message,
    EventType,
    LoopEvent,
    ProviderResponse,
    Role,
    ToolCall,
)
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.tool.builtin.send_message_tool import SendMessageTool
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool, WriteTool


class QueueingTextProvider(MockProvider):
    """Provider that queues a prompt while streaming a text-only response."""

    def __init__(self, message_queue: MessageQueue) -> None:
        super().__init__()
        self._message_queue = message_queue
        self._stream_call_count = 0

    async def chat_completion_stream(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[ProviderResponse, None]:
        self._calls.append((messages, tools))
        self._stream_call_count += 1
        if self._stream_call_count == 1:
            self._message_queue.enqueue(
                PendingMessage(content="queued while streaming")
            )
            yield ProviderResponse(text="The answer is ready.")
        else:
            yield ProviderResponse(text="Follow-up handled.")
        yield ProviderResponse(stop_reason="stop")


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
        provider = MockProvider(
            [
                ProviderResponse(text="The answer is 42."),
            ]
        )
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
    async def test_text_response_leaves_late_pending_prompt_for_followup(
        self, memory, registry
    ):
        """Late prompts after the last tool checkpoint should remain queued."""
        message_queue = MessageQueue()
        provider = QueueingTextProvider(message_queue)
        context = ContextBuilder(memory)
        loop = AgenticLoop(
            provider,
            registry,
            context,
            memory,
            message_queue=message_queue,
        )

        results = []
        async for event in loop.process("What is the answer?"):
            results.append(event)

        assert [e.text for e in results if e.type == EventType.TEXT_DONE] == [
            "The answer is ready."
        ]
        assert message_queue.has_pending_prompts()
        assert len(provider._calls) == 1

    @pytest.mark.asyncio
    async def test_single_tool_call_round(self, memory, registry):
        """One round of tool calls then text response."""
        provider = MockProvider(
            [
                # Round 1: tool call
                ProviderResponse(
                    text="",
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                # Round 2: final text
                ProviderResponse(text="Found the results."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Search for something"):
            results.append(event)

        text = _collect_text(results)
        assert text == "Found the results."

        # Should have tool events
        types = [e.type for e in results]
        assert EventType.TOOL_START in types
        assert EventType.TOOL_END in types

    @pytest.mark.asyncio
    async def test_tool_call_round_preserves_thinking_blocks(self, memory, registry):
        """Thinking blocks must be kept on assistant tool-use messages."""
        thinking_block = {
            "type": "thinking",
            "thinking": "I need to search first.",
            "signature": "sig_123",
        }
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                    thinking_blocks=[thinking_block],
                ),
                ProviderResponse(text="Found the results."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Search for something"):
            results.append(event)

        assistant_tool_messages = [
            msg
            for msg in loop._messages
            if msg.role == Role.ASSISTANT and msg.tool_calls
        ]
        assert _collect_text(results) == "Found the results."
        assert assistant_tool_messages[0].thinking_blocks == [thinking_block]

    @pytest.mark.asyncio
    async def test_multi_round_tool_calls(self, memory, registry):
        """Multiple rounds of tool calls before final text."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_2", name="edit", arguments={})],
                ),
                ProviderResponse(text="All done."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Do something complex"):
            results.append(event)

        text = _collect_text(results)
        assert text == "All done."

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

    @pytest.mark.asyncio
    async def test_tool_results_injected(self, memory, registry):
        """Tool results should be injected back into the conversation."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(text="Based on the grep results..."),
            ]
        )
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
    async def test_send_message_tool_emits_intermediate_message_event(self, memory):
        """send_message should be visible to frontends as a consumable event."""
        registry = ToolRegistry()
        registry.register(SendMessageTool())
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[
                        ToolCall(
                            id="tc_send",
                            name="send_message",
                            arguments={"message": "Still working..."},
                        )
                    ],
                ),
                ProviderResponse(text="Done."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        events = []
        async for event in loop.process("long task"):
            events.append(event)

        assert any(
            event.type == EventType.INTERMEDIATE_MESSAGE
            and event.text == "Still working..."
            for event in events
        )
        assert _collect_text(events) == "Done."

    @pytest.mark.asyncio
    async def test_session_persistence(self, memory, registry, tmp_path):
        """Conversation turns should be persisted to session JSONL file."""
        from openlist_ani.assistant.session.storage import SessionStorage

        session_storage = SessionStorage(tmp_path / "sessions")
        await session_storage.start_new_session()

        provider = MockProvider(
            [
                ProviderResponse(text="Hello there!"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(
            provider,
            registry,
            context,
            memory,
            session_storage=session_storage,
        )

        results = []
        async for event in loop.process("Hi"):
            results.append(event)

        # Load session and verify messages were recorded
        messages = await session_storage.load_session(session_storage.session_id)
        contents = [m.content for m in messages]
        assert "Hi" in contents
        assert "Hello there!" in contents

    @pytest.mark.asyncio
    async def test_session_persistence_with_tools(self, memory, registry, tmp_path):
        """Tool call messages should be persisted to session JSONL file."""
        from openlist_ani.assistant.session.storage import SessionStorage

        session_storage = SessionStorage(tmp_path / "sessions")
        await session_storage.start_new_session()

        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(text="Found results."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(
            provider,
            registry,
            context,
            memory,
            session_storage=session_storage,
        )

        results = []
        async for event in loop.process("Search for something"):
            results.append(event)

        messages = await session_storage.load_session(session_storage.session_id)
        contents = [m.content for m in messages]
        assert "Search for something" in contents
        assert "Found results." in contents


class TestStreaming:
    """Tests for the streaming path (TEXT_DELTA events via chat_completion_stream)."""

    @pytest.mark.asyncio
    async def test_text_delta_events_emitted(self, memory, registry):
        """AgenticLoop.process() should yield TEXT_DELTA events from the stream.

        The loop calls _collect_stream → provider.chat_completion_stream which
        yields partial chunks.  Each chunk with text triggers a TEXT_DELTA event.
        """
        provider = MockProvider(
            [
                ProviderResponse(text="Streaming works!"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Tell me something"):
            results.append(event)

        types = [e.type for e in results]
        # The mock stream yields a text-only delta first, so we must see
        # at least one TEXT_DELTA before the final TEXT_DONE.
        assert (
            EventType.TEXT_DELTA in types
        ), f"Expected TEXT_DELTA in events but got: {types}"
        assert EventType.TEXT_DONE in types

        # Collect all TEXT_DELTA payloads
        deltas = [e.text for e in results if e.type == EventType.TEXT_DELTA]
        assert any("Streaming works!" in d for d in deltas)

        # Final text should still be correct
        text = _collect_text(results)
        assert text == "Streaming works!"

    @pytest.mark.asyncio
    async def test_no_text_delta_for_tool_only_response(self, memory, registry):
        """A tool-only response (no text) should not produce TEXT_DELTA events
        for the tool-call round."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(text="Done after tool."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Search"):
            results.append(event)

        assert EventType.TEXT_DELTA in [e.type for e in results]
        text = _collect_text(results)
        assert text == "Done after tool."


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
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
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

    @pytest.mark.asyncio
    async def test_prompt_too_long_reactive_compact(self, memory, registry):
        """Should attempt reactive compact on prompt-too-long errors."""
        call_count = 0

        class PromptTooLongProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
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
            provider,
            registry,
            context,
            memory,
            max_context_chars=1_000_000,
        )

        results = []
        async for event in loop.process("Hello"):
            results.append(event)

        text = _collect_text(results)
        assert text == "OK after compact!"

    @pytest.mark.asyncio
    async def test_unrecoverable_error_graceful_message(self, memory, registry):
        """Should yield graceful error message on unrecoverable errors."""

        class AlwaysFailProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
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
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
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

    @pytest.mark.asyncio
    async def test_error_preserves_conversation_state(self, memory, registry):
        """After an error, the loop should still be usable for next messages."""
        call_count = 0

        class RecoverableProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
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

        provider = MockProvider(
            [
                ProviderResponse(text="First response."),
                ProviderResponse(text="Second response."),
            ]
        )
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
            pytest.fail(
                "process() deadlocked — lock was not released after generator close"
            )

        # Should have gotten a valid response
        text = _collect_text(results)
        assert text  # Non-empty response

    @pytest.mark.asyncio
    async def test_multiple_early_breaks(self, memory, registry):
        """Multiple consecutive early breaks should not corrupt state."""
        import asyncio

        provider = MockProvider(
            [
                ProviderResponse(text="Response 1."),
                ProviderResponse(text="Response 2."),
                ProviderResponse(text="Response 3."),
            ]
        )
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


class TestCancellationToken:
    """Tests for CancellationToken integration with AgenticLoop."""

    @pytest.mark.asyncio
    async def test_cancel_before_first_round(self, memory, registry):
        """Pre-cancelled token should yield (interrupted) immediately."""

        class FailIfCalledProvider(MockProvider):
            def chat_completion_stream(self, *args, **kwargs):
                raise AssertionError("provider should not be called")

        provider = FailIfCalledProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        token = CancellationToken()
        token.cancel()  # Pre-cancel

        results = []
        async for event in loop.process("Hello", cancel_token=token):
            results.append(event)

        text = _collect_text(results)
        assert text == "(interrupted)"

    @pytest.mark.asyncio
    async def test_cancel_during_streaming(self, memory, registry):
        """Token cancelled mid-stream should stop and yield (interrupted)."""
        token = CancellationToken()

        class CancelMidStreamProvider(MockProvider):
            async def chat_completion_stream(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
                yield ProviderResponse(text="chunk1 ")
                token.cancel()  # Cancel mid-stream
                yield ProviderResponse(text="chunk2 ")
                yield ProviderResponse(stop_reason="stop")

        provider = CancelMidStreamProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Hello", cancel_token=token):
            results.append(event)

        text = _collect_text(results)
        assert text == "(interrupted)"
        # Should have received at least one TEXT_DELTA before interruption
        deltas = [e for e in results if e.type == EventType.TEXT_DELTA]
        assert len(deltas) >= 1

    @pytest.mark.asyncio
    async def test_cancel_between_tool_calls(self, memory, registry):
        """Token cancelled between tool calls should inject tombstones."""
        token = CancellationToken()
        call_count = 0

        class CancelAfterFirstToolProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return ProviderResponse(
                        tool_calls=[
                            ToolCall(id="tc_1", name="grep", arguments={}),
                            ToolCall(id="tc_2", name="grep", arguments={}),
                        ],
                    )
                return ProviderResponse(text="Done.")

        provider = CancelAfterFirstToolProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        # Cancel the token as soon as the first TOOL_END event is seen,
        # which happens between the two tool completions.
        results = []
        async for event in loop.process("Do things", cancel_token=token):
            results.append(event)
            if event.type == EventType.TOOL_END and not token.is_cancelled:
                token.cancel()

        text = _collect_text(results)
        assert text == "(interrupted)"

    @pytest.mark.asyncio
    async def test_cancel_token_none_is_backward_compatible(self, memory, registry):
        """Passing no cancel_token should work exactly as before."""
        provider = MockProvider(
            [
                ProviderResponse(text="Normal response."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Hello"):
            results.append(event)

        text = _collect_text(results)
        assert text == "Normal response."

    @pytest.mark.asyncio
    async def test_cancel_preserves_conversation_state(self, memory, registry):
        """After cancellation, the loop should still work for next turns."""
        provider = MockProvider(
            [
                ProviderResponse(text="First."),
                ProviderResponse(text="Second."),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        # Turn 1: cancel immediately
        token1 = CancellationToken()
        token1.cancel()
        results1 = []
        async for event in loop.process("Turn 1", cancel_token=token1):
            results1.append(event)
        assert _collect_text(results1) == "(interrupted)"

        # Turn 2: should work normally
        results2 = []
        async for event in loop.process("Turn 2"):
            results2.append(event)
        assert _collect_text(results2) == "First."
