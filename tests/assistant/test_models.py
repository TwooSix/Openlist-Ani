"""Tests for core data models."""

from openlist_ani.assistant.core.models import (
    Message,
    ProviderResponse,
    Role,
    ToolCall,
    ToolResult,
)


def test_role_enum():
    assert Role.SYSTEM == "system"
    assert Role.USER == "user"
    assert Role.ASSISTANT == "assistant"
    assert Role.TOOL == "tool"


def test_tool_call_creation():
    tc = ToolCall(id="tc_1", name="grep", arguments={"pattern": "foo"})
    assert tc.id == "tc_1"
    assert tc.name == "grep"
    assert tc.arguments == {"pattern": "foo"}


def test_tool_result_creation():
    tr = ToolResult(
        tool_call_id="tc_1",
        name="grep",
        content="found 3 matches",
    )
    assert tr.tool_call_id == "tc_1"
    assert tr.is_error is False


def test_tool_result_error():
    tr = ToolResult(
        tool_call_id="tc_1",
        name="grep",
        content="file not found",
        is_error=True,
    )
    assert tr.is_error is True


def test_message_defaults():
    msg = Message(role=Role.USER, content="hello")
    assert msg.tool_calls == []
    assert msg.tool_results == []


def test_message_with_tool_calls():
    tc = ToolCall(id="tc_1", name="read", arguments={})
    msg = Message(role=Role.ASSISTANT, tool_calls=[tc])
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].name == "read"


def test_message_with_tool_results():
    tr = ToolResult(tool_call_id="tc_1", name="read", content="data")
    msg = Message(role=Role.TOOL, tool_results=[tr])
    assert len(msg.tool_results) == 1


def test_provider_response_defaults():
    resp = ProviderResponse()
    assert resp.text == ""
    assert resp.tool_calls == []
    assert resp.stop_reason == ""
    assert resp.usage == {}


def test_provider_response_with_data():
    tc = ToolCall(id="tc_1", name="grep", arguments={})
    resp = ProviderResponse(
        text="found it",
        tool_calls=[tc],
        stop_reason="tool_use",
        usage={"prompt_tokens": 100, "completion_tokens": 50},
    )
    assert resp.text == "found it"
    assert len(resp.tool_calls) == 1
    assert resp.usage["prompt_tokens"] == 100
