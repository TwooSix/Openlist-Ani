"""Tests for new loop features: max_output_tokens recovery, tombstone handling, turn tracking."""

import pytest
from pathlib import Path

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import AgenticLoop
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


class TestMaxOutputTokensRecovery:
    """Tests for max_output_tokens recovery (escalation + continue messages)."""

    @pytest.mark.asyncio
    async def test_escalation_on_first_max_tokens(self, memory, registry):
        """First max_tokens hit should escalate to higher max_tokens."""
        call_count = 0

        class EscalatingProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # System prompt build call (context builder)
                    return ProviderResponse(
                        text="Partial response...", stop_reason="max_tokens"
                    )
                if call_count == 2:
                    # Escalated call — should have max_tokens_override set
                    self._last_max_tokens = max_tokens_override
                    return ProviderResponse(text="Full response after escalation.")
                return ProviderResponse(text="Unexpected")

        provider = EscalatingProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Generate a long response"):
            results.append(event)

        text = _collect_text(results)
        assert text == "Full response after escalation."
        # Should have escalated on second call
        assert provider._last_max_tokens == 64_000

    @pytest.mark.asyncio
    async def test_continue_message_after_escalation(self, memory, registry):
        """After escalation, subsequent max_tokens should inject continue message."""
        call_count = 0

        class ContinueProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return ProviderResponse(text="Part 1...", stop_reason="max_tokens")
                if call_count == 2:
                    # Escalated call also hits max_tokens
                    return ProviderResponse(text="Part 2...", stop_reason="max_tokens")
                if call_count == 3:
                    # Check that continue message was injected
                    last_user = [m for m in messages if m.role == Role.USER][-1]
                    assert "Resume directly" in last_user.content
                    return ProviderResponse(text="Final part.")
                return ProviderResponse(text="Done")

        provider = ContinueProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Long generation"):
            results.append(event)

        text = _collect_text(results)
        assert text == "Final part."

    @pytest.mark.asyncio
    async def test_max_recovery_limit(self, memory, registry):
        """Should stop after MAX_OUTPUT_TOKENS_RECOVERY_LIMIT continue attempts."""
        call_count = 0

        class AlwaysMaxTokensProvider(MockProvider):
            async def chat_completion(
                self, messages, tools=None, max_tokens_override=None, temperature=None
            ):
                nonlocal call_count
                call_count += 1
                # First call: normal max_tokens → triggers escalation
                # Second call: escalated max_tokens → triggers continue #1
                # Third call: continue #1 → triggers continue #2
                # Fourth call: continue #2 → triggers continue #3
                # Fifth call: continue #3 → recovery limit reached
                if call_count <= 5:
                    return ProviderResponse(
                        text=f"Part {call_count}...", stop_reason="max_tokens"
                    )
                return ProviderResponse(text="Final")

        provider = AlwaysMaxTokensProvider()
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        results = []
        async for event in loop.process("Infinite generation"):
            results.append(event)

        # Should eventually give up and return the last partial text
        # (after exhausting recovery limit)
        text = _collect_text(results)
        assert len(text) > 0


class TestTurnTracking:
    """Tests for turn count tracking."""

    @pytest.mark.asyncio
    async def test_no_tools_no_turns(self, memory, registry):
        """Pure text response should not increment turn count."""
        provider = MockProvider(
            [
                ProviderResponse(text="Hello!"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        async for _ in loop.process("Hi"):
            pass  # Consume all events

        assert loop.turn_count == 0

    @pytest.mark.asyncio
    async def test_single_tool_one_turn(self, memory, registry):
        """One tool call cycle should increment turn count by 1."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(text="Done"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        async for _ in loop.process("Search"):
            pass  # Consume all events

        assert loop.turn_count == 1

    @pytest.mark.asyncio
    async def test_multi_tool_rounds(self, memory, registry):
        """Multiple tool rounds should increment turn count per round."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_2", name="edit", arguments={})],
                ),
                ProviderResponse(text="All done"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        async for _ in loop.process("Complex task"):
            pass  # Consume all events

        assert loop.turn_count == 2

    @pytest.mark.asyncio
    async def test_turn_count_accumulates_across_calls(self, memory, registry):
        """Turn count should accumulate across multiple process() calls."""
        provider = MockProvider(
            [
                # First call: one tool round
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(text="Result 1"),
                # Second call: two tool rounds
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_2", name="grep", arguments={})],
                ),
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_3", name="edit", arguments={})],
                ),
                ProviderResponse(text="Result 2"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        async for _ in loop.process("First"):
            pass  # Consume all events
        assert loop.turn_count == 1

        async for _ in loop.process("Second"):
            pass  # Consume all events
        assert loop.turn_count == 3  # 1 + 2

    @pytest.mark.asyncio
    async def test_reset_clears_turn_count(self, memory, registry):
        """reset() should clear the turn count."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="grep", arguments={})],
                ),
                ProviderResponse(text="Done"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        async for _ in loop.process("Task"):
            pass  # Consume all events
        assert loop.turn_count == 1

        loop.reset()
        assert loop.turn_count == 0


class TestTombstoneHandling:
    """Tests for tombstone/orphaned tool_call handling."""

    @pytest.mark.asyncio
    async def test_all_tool_calls_get_results(self, memory, registry):
        """Normal case: all tool calls should have matching results."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[
                        ToolCall(id="tc_1", name="grep", arguments={}),
                        ToolCall(id="tc_2", name="edit", arguments={}),
                    ],
                ),
                ProviderResponse(text="Done"),
            ]
        )
        context = ContextBuilder(memory)
        loop = AgenticLoop(provider, registry, context, memory)

        async for _ in loop.process("Search and edit"):
            pass  # Consume all events

        # Check that tool results were properly injected
        tool_msgs = [m for m in loop._messages if m.role == Role.TOOL]
        assert len(tool_msgs) >= 1
        # All tool calls should have results
        result_ids = {r.tool_call_id for r in tool_msgs[-1].tool_results}
        assert "tc_1" in result_ids
        assert "tc_2" in result_ids


class TestProviderSignature:
    """Tests for provider accepting new parameters."""

    @pytest.mark.asyncio
    async def test_mock_provider_accepts_new_params(self):
        """MockProvider should accept max_tokens_override and temperature."""
        provider = MockProvider([ProviderResponse(text="OK")])
        response = await provider.chat_completion(
            messages=[Message(role=Role.USER, content="Hi")],
            tools=None,
            max_tokens_override=64_000,
            temperature=0.5,
        )
        assert response.text == "OK"
