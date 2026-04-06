"""Tests for tool registry."""

import pytest

from openlist_ani.assistant.core.models import ToolCall
from openlist_ani.assistant.tool.base import BaseTool
from openlist_ani.assistant.tool.registry import (
    ToolRegistry,
    _validate_tool_input,
)

from .conftest import ErrorTool, ReadOnlyTool, WriteTool


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = ReadOnlyTool("my_tool")
        registry.register(tool)

        assert registry.get("my_tool") is tool
        assert registry.get("nonexistent") is None

    def test_register_duplicate_raises(self):
        registry = ToolRegistry()
        tool1 = ReadOnlyTool("dup")
        tool2 = ReadOnlyTool("dup")
        registry.register(tool1)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool2)

    def test_all_tools(self):
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("a"))
        registry.register(WriteTool("b"))

        tools = registry.all_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_dispatch_success(self):
        registry = ToolRegistry()
        tool = ReadOnlyTool("grep", result="found 5 matches")
        registry.register(tool)

        tc = ToolCall(id="tc_1", name="grep", arguments={})
        result = await registry.dispatch(tc)

        assert result.tool_call_id == "tc_1"
        assert result.name == "grep"
        assert result.content == "found 5 matches"
        assert result.is_error is False
        assert tool.call_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self):
        registry = ToolRegistry()
        tc = ToolCall(id="tc_1", name="nonexistent", arguments={})
        result = await registry.dispatch(tc)

        assert result.is_error is True
        assert "No such tool" in result.content

    @pytest.mark.asyncio
    async def test_dispatch_tool_error(self):
        registry = ToolRegistry()
        registry.register(ErrorTool())

        tc = ToolCall(id="tc_1", name="error_tool", arguments={})
        result = await registry.dispatch(tc)

        assert result.is_error is True
        assert "Tool execution failed" in result.content


class StrictTool(BaseTool):
    """A tool with a strict JSON Schema for testing validation."""

    @property
    def name(self) -> str:
        return "strict_tool"

    @property
    def description(self) -> str:
        return "A tool with strict parameters"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
                "verbose": {"type": "boolean"},
                "tags": {"type": "array"},
                "options": {"type": "object"},
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: object) -> str:
        return f"OK: {kwargs}"


class TestValidateToolInput:
    """Tests for _validate_tool_input()."""

    def test_valid_input(self):
        """Valid input passes validation."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        }
        assert _validate_tool_input("test", {"query": "hello"}, schema) is None

    def test_missing_required(self):
        """Missing required parameter returns error."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["query", "path"],
        }
        error = _validate_tool_input("test", {"query": "hello"}, schema)
        assert error is not None
        assert "Missing required" in error
        assert "path" in error

    def test_wrong_type_string(self):
        """Integer where string expected returns error."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
        }
        error = _validate_tool_input("test", {"query": 42}, schema)
        assert error is not None
        assert "expected string" in error
        assert "got int" in error

    def test_wrong_type_integer(self):
        """String where integer expected returns error."""
        schema = {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
        }
        error = _validate_tool_input("test", {"limit": "ten"}, schema)
        assert error is not None
        assert "expected integer" in error

    def test_wrong_type_boolean(self):
        """String where boolean expected returns error."""
        schema = {
            "type": "object",
            "properties": {
                "verbose": {"type": "boolean"},
            },
        }
        error = _validate_tool_input("test", {"verbose": "yes"}, schema)
        assert error is not None
        assert "expected boolean" in error

    def test_wrong_type_array(self):
        """String where array expected returns error."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array"},
            },
        }
        error = _validate_tool_input("test", {"tags": "a,b,c"}, schema)
        assert error is not None
        assert "expected array" in error

    def test_extra_params_tolerated(self):
        """Extra parameters not in schema are tolerated."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
        }
        assert _validate_tool_input(
            "test", {"query": "hello", "extra": 42}, schema
        ) is None

    def test_empty_schema_passes(self):
        """Empty or missing schema passes everything."""
        assert _validate_tool_input("test", {"anything": 42}, {}) is None
        assert _validate_tool_input("test", {"anything": 42}, {"type": "string"}) is None

    def test_no_required_field(self):
        """Schema without required section passes empty args."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
        }
        assert _validate_tool_input("test", {}, schema) is None

    def test_number_accepts_int_and_float(self):
        """'number' type should accept both int and float."""
        schema = {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
            },
        }
        assert _validate_tool_input("test", {"score": 42}, schema) is None
        assert _validate_tool_input("test", {"score": 3.14}, schema) is None
        error = _validate_tool_input("test", {"score": "high"}, schema)
        assert error is not None


class TestDispatchValidation:
    """Tests for input validation during dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_with_valid_input(self):
        """Dispatch with valid input should succeed."""
        registry = ToolRegistry()
        registry.register(StrictTool())

        tc = ToolCall(id="tc_1", name="strict_tool", arguments={"query": "test"})
        result = await registry.dispatch(tc)

        assert result.is_error is False
        assert "OK:" in result.content

    @pytest.mark.asyncio
    async def test_dispatch_with_missing_required(self):
        """Dispatch with missing required param should return validation error."""
        registry = ToolRegistry()
        registry.register(StrictTool())

        tc = ToolCall(id="tc_1", name="strict_tool", arguments={"limit": 10})
        result = await registry.dispatch(tc)

        assert result.is_error is True
        assert "InputValidationError" in result.content
        assert "query" in result.content

    @pytest.mark.asyncio
    async def test_dispatch_with_wrong_type(self):
        """Dispatch with wrong type should return validation error."""
        registry = ToolRegistry()
        registry.register(StrictTool())

        tc = ToolCall(
            id="tc_1", name="strict_tool",
            arguments={"query": 42},  # should be string
        )
        result = await registry.dispatch(tc)

        assert result.is_error is True
        assert "InputValidationError" in result.content
        assert "expected string" in result.content
