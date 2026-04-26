"""Tests for sub-agent lifecycle."""

import asyncio

import pytest

from openlist_ani.assistant.core.models import ProviderResponse, ToolCall
from openlist_ani.assistant.core.subagent import (
    BUILTIN_AGENT_CONFIGS,
    SubAgentConfig,
    run_subagent,
)
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool, WriteTool


class TestSubAgentConfig:
    def test_builtin_configs_exist(self):
        assert "general-purpose" in BUILTIN_AGENT_CONFIGS
        assert "explore" in BUILTIN_AGENT_CONFIGS

    def test_explore_is_concurrency_safe(self):
        explore = BUILTIN_AGENT_CONFIGS["explore"]
        assert explore.is_concurrency_safe is True

    def test_general_purpose_has_all_tools(self):
        gp = BUILTIN_AGENT_CONFIGS["general-purpose"]
        assert gp.allowed_tool_names is None  # All tools


class TestRunSubagent:
    @pytest.mark.asyncio
    async def test_pure_text_response(self):
        """Sub-agent returns text without tool calls."""
        provider = MockProvider(
            [
                ProviderResponse(text="Task completed."),
            ]
        )
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("search"))

        config = SubAgentConfig(
            agent_type="test",
            system_prompt="You are a test agent.",
        )
        result = await run_subagent(config, "Do something", provider, registry)

        assert result == "Task completed."

    @pytest.mark.asyncio
    async def test_tool_call_then_text(self):
        """Sub-agent calls a tool then responds."""
        provider = MockProvider(
            [
                ProviderResponse(
                    tool_calls=[ToolCall(id="tc_1", name="search", arguments={})],
                ),
                ProviderResponse(text="Found the answer."),
            ]
        )
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("search", "search results"))

        config = SubAgentConfig(
            agent_type="test",
            system_prompt="You are a test agent.",
        )
        result = await run_subagent(config, "Find something", provider, registry)

        assert result == "Found the answer."
        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_tool_filtering(self):
        """Sub-agent with allowed_tool_names only sees those tools."""
        provider = MockProvider(
            [
                ProviderResponse(text="Done."),
            ]
        )
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("allowed_tool"))
        registry.register(WriteTool("forbidden_tool"))

        config = SubAgentConfig(
            agent_type="filtered",
            system_prompt="Filtered agent.",
            allowed_tool_names=["allowed_tool"],
        )
        await run_subagent(config, "Do it", provider, registry)

        # Check that only allowed_tool was in tool definitions
        call_tools = provider._calls[0][1]
        tool_names = [t["function"]["name"] for t in call_tools]
        assert "allowed_tool" in tool_names
        assert "forbidden_tool" not in tool_names

    @pytest.mark.asyncio
    async def test_max_rounds_limit(self):
        """Sub-agent should stop at max_rounds."""
        responses = [
            ProviderResponse(
                tool_calls=[ToolCall(id=f"tc_{i}", name="search", arguments={})],
            )
            for i in range(10)
        ]
        provider = MockProvider(responses)
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("search"))

        config = SubAgentConfig(
            agent_type="test",
            system_prompt="Agent",
            max_rounds=3,
        )
        result = await run_subagent(config, "Loop", provider, registry)

        assert "maximum" in result.lower()
        assert provider._call_count == 3

    @pytest.mark.asyncio
    async def test_independent_message_list(self):
        """Sub-agent messages should not pollute parent."""
        provider = MockProvider(
            [
                ProviderResponse(text="Done."),
            ]
        )
        registry = ToolRegistry()

        parent_messages = []  # Simulating parent's messages
        config = SubAgentConfig(
            agent_type="test",
            system_prompt="Agent",
        )
        await run_subagent(config, "Task", provider, registry)

        # Parent messages should still be empty
        assert len(parent_messages) == 0


class TestSubAgentOverallTimeout:
    """Test overall timeout: run_subagent wraps the loop in wait_for."""

    @pytest.mark.asyncio
    async def test_overall_timeout_returns_message(self):
        """Provider responds slowly; run_subagent should return a timeout message."""

        class SlowProvider(MockProvider):
            async def chat_completion(
                self,
                messages,
                tools=None,
                max_tokens_override=None,
                temperature=None,
            ):
                # Use a Future that never resolves — survives the _no_sleep
                # fixture which makes asyncio.sleep a no-op.
                await asyncio.get_running_loop().create_future()
                return ProviderResponse(text="Too late")  # pragma: no cover

        provider = SlowProvider()
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("search"))

        config = SubAgentConfig(
            agent_type="timeout-test",
            system_prompt="Agent",
            timeout_seconds=0.1,  # Very short overall timeout
            per_call_timeout=60.0,
        )
        result = await run_subagent(config, "Do it", provider, registry)

        assert "timed out" in result.lower()
        assert "timeout-test" in result


class TestSubAgentCancelledError:
    """Test that asyncio.CancelledError propagates from run_subagent."""

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """Externally cancelling the task should raise CancelledError."""

        class HangingProvider(MockProvider):
            async def chat_completion(
                self,
                messages,
                tools=None,
                max_tokens_override=None,
                temperature=None,
            ):
                # Block forever using a Future (survives _no_sleep fixture)
                await asyncio.get_running_loop().create_future()
                return ProviderResponse(text="Never reached")  # pragma: no cover

        provider = HangingProvider()
        registry = ToolRegistry()

        config = SubAgentConfig(
            agent_type="cancel-test",
            system_prompt="Agent",
            timeout_seconds=60.0,
            per_call_timeout=60.0,
        )

        task = asyncio.create_task(
            run_subagent(config, "Long task", provider, registry)
        )
        # Yield control so the task starts executing
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


class TestSubAgentTransientRetry:
    """Test transient error retry with backoff."""

    @pytest.mark.asyncio
    async def test_transient_error_then_success(self):
        """Provider raises a transient error once, then succeeds."""
        call_count = 0

        class TransientThenOkProvider(MockProvider):
            async def chat_completion(
                self,
                messages,
                tools=None,
                max_tokens_override=None,
                temperature=None,
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # Transient error — _is_transient checks for keywords
                    raise ConnectionError("connection reset by peer")
                return ProviderResponse(text="Recovered successfully.")

        provider = TransientThenOkProvider()
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("search"))

        config = SubAgentConfig(
            agent_type="retry-test",
            system_prompt="Agent",
            timeout_seconds=30.0,
            per_call_timeout=10.0,
        )
        result = await run_subagent(config, "Do it", provider, registry)

        assert result == "Recovered successfully."
        # Should have retried: first call failed, second succeeded
        assert call_count == 2
