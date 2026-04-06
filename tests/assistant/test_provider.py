"""Tests for provider message format conversion and tool definitions."""

import json

import pytest

from openlist_ani.assistant.core.models import (
    Message,
    Role,
    ToolCall,
    ToolResult,
)
from openlist_ani.assistant.provider.factory import create_provider
from openlist_ani.assistant.provider.openai_provider import OpenAIProvider
from openlist_ani.assistant.provider.anthropic_provider import AnthropicProvider

from .conftest import ReadOnlyTool, WriteTool


class TestOpenAIProvider:
    def test_format_tool_definitions(self):
        provider = OpenAIProvider(api_key="test", base_url="http://test", model="gpt-4o")
        tools = [ReadOnlyTool("grep"), WriteTool("edit")]
        defs = provider.format_tool_definitions(tools)

        assert len(defs) == 2
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "grep"
        assert defs[1]["function"]["name"] == "edit"

    def test_convert_messages_system(self):
        provider = OpenAIProvider(api_key="test", base_url="http://test", model="gpt-4o")
        messages = [Message(role=Role.SYSTEM, content="You are helpful.")]
        result = provider._convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_convert_messages_user(self):
        provider = OpenAIProvider(api_key="test", base_url="http://test", model="gpt-4o")
        messages = [Message(role=Role.USER, content="Hello")]
        result = provider._convert_messages(messages)

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_convert_messages_assistant_with_tool_calls(self):
        provider = OpenAIProvider(api_key="test", base_url="http://test", model="gpt-4o")
        tc = ToolCall(id="tc_1", name="grep", arguments={"pattern": "foo"})
        messages = [Message(role=Role.ASSISTANT, tool_calls=[tc])]
        result = provider._convert_messages(messages)

        assert result[0]["role"] == "assistant"
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["function"]["name"] == "grep"
        args = json.loads(result[0]["tool_calls"][0]["function"]["arguments"])
        assert args["pattern"] == "foo"

    def test_convert_messages_tool_results(self):
        provider = OpenAIProvider(api_key="test", base_url="http://test", model="gpt-4o")
        tr = ToolResult(tool_call_id="tc_1", name="grep", content="3 matches")
        messages = [Message(role=Role.TOOL, tool_results=[tr])]
        result = provider._convert_messages(messages)

        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc_1"
        assert result[0]["content"] == "3 matches"


class TestAnthropicProvider:
    def test_format_tool_definitions(self):
        provider = AnthropicProvider(api_key="test", base_url="http://test", model="claude")
        tools = [ReadOnlyTool("search")]
        defs = provider.format_tool_definitions(tools)

        assert len(defs) == 1
        assert defs[0]["name"] == "search"
        assert "input_schema" in defs[0]

    def test_convert_messages_system_separated(self):
        provider = AnthropicProvider(api_key="test", base_url="http://test", model="claude")
        messages = [
            Message(role=Role.SYSTEM, content="You are helpful."),
            Message(role=Role.USER, content="Hi"),
        ]
        system_prompt, api_messages = provider._convert_messages(messages)

        assert system_prompt == "You are helpful."
        assert len(api_messages) == 1
        assert api_messages[0]["role"] == "user"

    def test_convert_messages_assistant_with_tool_use(self):
        provider = AnthropicProvider(api_key="test", base_url="http://test", model="claude")
        tc = ToolCall(id="tc_1", name="read", arguments={"path": "/tmp"})
        messages = [Message(role=Role.ASSISTANT, tool_calls=[tc], content="Let me read that.")]
        _, api_messages = provider._convert_messages(messages)

        assert api_messages[0]["role"] == "assistant"
        content = api_messages[0]["content"]
        # Should have text block and tool_use block
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"
        assert content[1]["name"] == "read"

    def test_convert_messages_tool_results_as_user(self):
        provider = AnthropicProvider(api_key="test", base_url="http://test", model="claude")
        tr = ToolResult(tool_call_id="tc_1", name="read", content="file data")
        messages = [Message(role=Role.TOOL, tool_results=[tr])]
        _, api_messages = provider._convert_messages(messages)

        # Anthropic wraps tool_result in a user message
        assert api_messages[0]["role"] == "user"
        content = api_messages[0]["content"]
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "tc_1"


class TestProviderFactory:
    def test_create_openai_provider(self):
        provider = create_provider("openai", "key", "http://url", "gpt-4o")
        assert isinstance(provider, OpenAIProvider)

    def test_create_anthropic_provider(self):
        provider = create_provider("anthropic", "key", "http://url", "claude")
        assert isinstance(provider, AnthropicProvider)

    def test_create_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider_type"):
            create_provider("unknown", "key", "http://url", "model")
