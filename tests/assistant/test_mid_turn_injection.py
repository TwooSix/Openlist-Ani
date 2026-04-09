"""Tests for mid-turn user message injection.

Covers:
- MessageQueue basic operations
- AgenticLoop mid-turn interruption
- Tombstone injection for interrupted tools
- User message injection into conversation history
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.core.message_queue import MessageQueue, PendingMessage
from openlist_ani.assistant.core.models import (
    EventType,
    LoopEvent,
    Message,
    ProviderResponse,
    Role,
    ToolCall,
)
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool, WriteTool


# ── MessageQueue unit tests ──────────────────────────────────────────


class TestMessageQueue:
    def test_enqueue_and_has_pending(self):
        """Enqueued prompt messages should be detectable."""
        q = MessageQueue()
        assert not q.has_pending_prompts()

        q.enqueue(PendingMessage(content="hello"))
        assert q.has_pending_prompts()

    def test_drain_prompts_returns_and_removes(self):
        """drain_prompts should return prompt messages and remove them."""
        q = MessageQueue()
        q.enqueue(PendingMessage(content="msg1"))
        q.enqueue(PendingMessage(content="msg2"))

        drained = q.drain_prompts()
        assert len(drained) == 2
        assert drained[0].content == "msg1"
        assert drained[1].content == "msg2"
        assert not q.has_pending_prompts()
        assert len(q) == 0

    def test_drain_prompts_leaves_notifications(self):
        """drain_prompts should leave notification messages in the queue."""
        q = MessageQueue()
        q.enqueue(PendingMessage(content="user msg", mode="prompt"))
        q.enqueue(PendingMessage(content="system notify", mode="notification"))
        q.enqueue(PendingMessage(content="user msg 2", mode="prompt"))

        drained = q.drain_prompts()
        assert len(drained) == 2
        assert drained[0].content == "user msg"
        assert drained[1].content == "user msg 2"

        # Notification should remain
        assert len(q) == 1
        assert not q.has_pending_prompts()

    def test_clear(self):
        """clear should remove all messages."""
        q = MessageQueue()
        q.enqueue(PendingMessage(content="a"))
        q.enqueue(PendingMessage(content="b", mode="notification"))
        q.clear()
        assert len(q) == 0
        assert not q.has_pending_prompts()

    def test_empty_drain(self):
        """Draining an empty queue should return empty list."""
        q = MessageQueue()
        assert q.drain_prompts() == []

    def test_bool_and_len(self):
        """__bool__ and __len__ should reflect queue state."""
        q = MessageQueue()
        assert not q
        assert len(q) == 0

        q.enqueue(PendingMessage(content="x"))
        assert q
        assert len(q) == 1


# ── CallbackWriteTool for testing mid-turn interruption ──────────────


class CallbackWriteTool(WriteTool):
    """A write tool that calls an on_complete callback after execution.

    The callback runs inline (deterministically) during execute(),
    which avoids relying on asyncio background task scheduling that
    behaves differently under pytest-asyncio.
    """

    def __init__(
        self,
        name: str = "callback_write",
        result: str = "done",
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(name, result)
        self.on_complete = on_complete

    async def execute(self, **kwargs) -> str:
        result = await super().execute(**kwargs)
        if self.on_complete is not None:
            self.on_complete()
        return result


# ── AgenticLoop mid-turn interruption tests ──────────────────────────


@pytest.fixture
def memory(tmp_path: Path):
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    return MemoryManager(data_dir=data_dir, project_root=project)


def _collect_text(events: list[LoopEvent]) -> str:
    """Extract final text from a list of LoopEvents."""
    for event in reversed(events):
        if event.type == EventType.TEXT_DONE:
            return event.text
    return ""


class TestMidTurnInterruption:
    @pytest.mark.asyncio
    async def test_no_interruption_when_queue_empty(self, memory):
        """Without pending messages, all tools execute normally."""
        registry = ToolRegistry()
        w1 = WriteTool("w1", "result_1")
        w2 = WriteTool("w2", "result_2")
        registry.register(w1)
        registry.register(w2)

        provider = MockProvider([
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="tc_1", name="w1", arguments={}),
                    ToolCall(id="tc_2", name="w2", arguments={}),
                ],
            ),
            ProviderResponse(text="All done."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        events = []
        async for event in loop.process("Do things"):
            events.append(event)

        text = _collect_text(events)
        assert text == "All done."
        assert w1.call_count == 1
        assert w2.call_count == 1

        # No USER_MESSAGE_INJECTED events
        injected = [e for e in events if e.type == EventType.USER_MESSAGE_INJECTED]
        assert len(injected) == 0

    @pytest.mark.asyncio
    async def test_interruption_injects_user_message(self, memory):
        """When a user message is queued mid-turn, remaining tools are
        interrupted and the user message is injected into context."""
        mq = MessageQueue()

        # w1's on_complete callback enqueues a user message directly
        def _enqueue():
            mq.enqueue(PendingMessage(content="Hey, stop that!"))

        registry = ToolRegistry()
        w1 = CallbackWriteTool("w1", "result_1", on_complete=_enqueue)
        w2 = WriteTool("w2", "result_2")
        registry.register(w1)
        registry.register(w2)

        provider = MockProvider([
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="tc_1", name="w1", arguments={}),
                    ToolCall(id="tc_2", name="w2", arguments={}),
                ],
            ),
            ProviderResponse(text="I see your new message!"),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory, message_queue=mq)

        events = []
        async for event in loop.process("Do things"):
            events.append(event)

        # w1 should have executed, w2 should NOT have executed
        assert w1.call_count == 1
        assert w2.call_count == 0

        # Should have USER_MESSAGE_INJECTED event
        injected = [e for e in events if e.type == EventType.USER_MESSAGE_INJECTED]
        assert len(injected) == 1
        assert injected[0].text == "Hey, stop that!"

        # Provider should have been called twice (tool round + follow-up)
        assert provider._call_count == 2

        # The second call's messages should contain the user's injected message
        second_call_msgs = provider._calls[1][0]
        user_msgs = [m for m in second_call_msgs if m.role == Role.USER]
        assert any("Hey, stop that!" in m.content for m in user_msgs)

    @pytest.mark.asyncio
    async def test_tombstone_injected_for_interrupted_tools(self, memory):
        """Interrupted tools should get tombstone (is_error=True) results."""
        mq = MessageQueue()

        def _enqueue():
            mq.enqueue(PendingMessage(content="interrupt"))

        registry = ToolRegistry()
        w1 = CallbackWriteTool("w1", "result_1", on_complete=_enqueue)
        w2 = WriteTool("w2", "result_2")
        w3 = WriteTool("w3", "result_3")
        registry.register(w1)
        registry.register(w2)
        registry.register(w3)

        provider = MockProvider([
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="tc_1", name="w1", arguments={}),
                    ToolCall(id="tc_2", name="w2", arguments={}),
                    ToolCall(id="tc_3", name="w3", arguments={}),
                ],
            ),
            ProviderResponse(text="OK"),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory, message_queue=mq)

        events = []
        async for event in loop.process("Do many things"):
            events.append(event)

        # Check that the tool results in the second API call include tombstones
        second_call_msgs = provider._calls[1][0]
        tool_msgs = [m for m in second_call_msgs if m.role == Role.TOOL]
        assert len(tool_msgs) >= 1

        # The tool message should have results for all 3 tool calls
        last_tool_msg = tool_msgs[-1]
        assert len(last_tool_msg.tool_results) == 3

        # w1: normal result
        w1_result = next(r for r in last_tool_msg.tool_results if r.tool_call_id == "tc_1")
        assert not w1_result.is_error
        assert w1_result.content == "result_1"

        # w2 and w3: tombstones
        w2_result = next(r for r in last_tool_msg.tool_results if r.tool_call_id == "tc_2")
        assert w2_result.is_error
        assert "interrupted" in w2_result.content.lower()

        w3_result = next(r for r in last_tool_msg.tool_results if r.tool_call_id == "tc_3")
        assert w3_result.is_error
        assert "interrupted" in w3_result.content.lower()

    @pytest.mark.asyncio
    async def test_multiple_pending_messages_all_injected(self, memory):
        """Multiple queued messages should all be injected."""
        mq = MessageQueue()

        def _enqueue_multiple():
            mq.enqueue(PendingMessage(content="Message 1"))
            mq.enqueue(PendingMessage(content="Message 2"))
            mq.enqueue(PendingMessage(content="Message 3"))

        registry = ToolRegistry()
        w1 = CallbackWriteTool("w1", "done", on_complete=_enqueue_multiple)
        registry.register(w1)

        provider = MockProvider([
            ProviderResponse(
                tool_calls=[ToolCall(id="tc_1", name="w1", arguments={})],
            ),
            ProviderResponse(text="Got all messages."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory, message_queue=mq)

        events = []
        async for event in loop.process("Start"):
            events.append(event)

        # All 3 messages should be injected
        injected = [e for e in events if e.type == EventType.USER_MESSAGE_INJECTED]
        assert len(injected) == 3
        assert [e.text for e in injected] == ["Message 1", "Message 2", "Message 3"]

    @pytest.mark.asyncio
    async def test_notification_does_not_trigger_interruption(self, memory):
        """Notification-mode messages should NOT trigger mid-turn interruption."""
        mq = MessageQueue()

        def _enqueue_notification():
            mq.enqueue(PendingMessage(content="system notify", mode="notification"))

        registry = ToolRegistry()
        w1 = CallbackWriteTool("w1", "result_1", on_complete=_enqueue_notification)
        w2 = WriteTool("w2", "result_2")
        registry.register(w1)
        registry.register(w2)

        provider = MockProvider([
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="tc_1", name="w1", arguments={}),
                    ToolCall(id="tc_2", name="w2", arguments={}),
                ],
            ),
            ProviderResponse(text="All done normally."),
        ])
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory, message_queue=mq)

        events = []
        async for event in loop.process("Do things"):
            events.append(event)

        # Both tools should have executed (no interruption for notifications)
        assert w1.call_count == 1
        assert w2.call_count == 1

        # No USER_MESSAGE_INJECTED events
        injected = [e for e in events if e.type == EventType.USER_MESSAGE_INJECTED]
        assert len(injected) == 0

        text = _collect_text(events)
        assert text == "All done normally."
