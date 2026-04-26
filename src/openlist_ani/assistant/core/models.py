"""
Shared data models for the assistant module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    """Message role in the conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str  # Provider-assigned unique ID
    name: str  # Tool name
    arguments: dict  # Parsed arguments

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCall":
        return cls(
            id=data["id"], name=data["name"], arguments=data.get("arguments", {})
        )


@dataclass
class ToolResult:
    """Result of executing a tool call."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False

    def to_dict(self) -> dict:
        return {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": self.content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolResult":
        return cls(
            tool_call_id=data["tool_call_id"],
            name=data["name"],
            content=data.get("content", ""),
            is_error=data.get("is_error", False),
        )


@dataclass
class Message:
    """Unified message format used throughout the assistant pipeline."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    reasoning_content: str | None = None
    # Anthropic-format thinking blocks (list of {"type": "thinking", "thinking": ..., "signature": ...})
    thinking_blocks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_results:
            d["tool_results"] = [tr.to_dict() for tr in self.tool_results]
        if self.reasoning_content is not None:
            d["reasoning_content"] = self.reasoning_content
        if self.thinking_blocks:
            d["thinking_blocks"] = self.thinking_blocks
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=Role(data["role"]),
            content=data.get("content", ""),
            tool_calls=[ToolCall.from_dict(tc) for tc in data.get("tool_calls", [])],
            tool_results=[
                ToolResult.from_dict(tr) for tr in data.get("tool_results", [])
            ],
            reasoning_content=data.get("reasoning_content"),
            thinking_blocks=data.get("thinking_blocks", []),
        )


@dataclass
class ProviderResponse:
    """Unified response from any LLM provider."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None
    # Anthropic-format thinking blocks for pass-back
    thinking_blocks: list[dict] = field(default_factory=list)


class EventType(str, Enum):
    """Type of event yielded by AgenticLoop.process()."""

    THINKING = "thinking"
    TEXT_DELTA = "text_delta"
    TEXT_DONE = "text_done"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    ERROR = "error"
    USER_MESSAGE_INJECTED = "user_message_injected"


@dataclass
class LoopEvent:
    """Event yielded by AgenticLoop.process() for real-time UI updates.

    Replaces the previous plain-string yields with structured events
    so frontends can display tool activity, streaming text, etc.
    """

    type: EventType
    text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result_preview: str = ""
    activity: str = ""
