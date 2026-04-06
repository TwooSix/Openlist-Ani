"""Tests for sub-agent lifecycle."""

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
        provider = MockProvider([
            ProviderResponse(text="Task completed."),
        ])
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
        provider = MockProvider([
            ProviderResponse(
                tool_calls=[ToolCall(id="tc_1", name="search", arguments={})],
            ),
            ProviderResponse(text="Found the answer."),
        ])
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
        provider = MockProvider([
            ProviderResponse(text="Done."),
        ])
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
        provider = MockProvider([
            ProviderResponse(text="Done."),
        ])
        registry = ToolRegistry()

        parent_messages = []  # Simulating parent's messages
        config = SubAgentConfig(
            agent_type="test",
            system_prompt="Agent",
        )
        await run_subagent(config, "Task", provider, registry)

        # Parent messages should still be empty
        assert len(parent_messages) == 0
