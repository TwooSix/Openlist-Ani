"""Tests for core data models."""

from openlist_ani.assistant.core.models import (
    ProviderResponse,
    ToolCall,
    ToolResult,
)


def test_tool_result_error():
    tr = ToolResult(
        tool_call_id="tc_1",
        name="grep",
        content="file not found",
        is_error=True,
    )
    assert tr.is_error is True


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
