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


@dataclass
class ToolResult:
    """Result of executing a tool call."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """Unified message format used throughout the assistant pipeline."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class ProviderResponse:
    """Unified response from any LLM provider."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class EventType(str, Enum):
    """Type of event yielded by AgenticLoop.process()."""

    THINKING = "thinking"
    TEXT_DELTA = "text_delta"
    TEXT_DONE = "text_done"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    ERROR = "error"


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
