"""
Tool registry — registration, lookup, and dispatch.
"""

from __future__ import annotations

import traceback

from openlist_ani.assistant.core.models import ToolCall, ToolResult

from .base import BaseTool

from loguru import logger


def _validate_tool_input(
    tool_name: str,
    arguments: dict,
    schema: dict,
) -> str | None:
    """Validate tool arguments against the tool's JSON Schema.

    Lightweight validation (no jsonschema dependency) that catches
    the most common model errors:
    1. Missing required parameters
    2. Wrong parameter types (string vs int vs bool vs array vs object)
    3. Extra parameters not in schema (warning only, not rejected)

    Args:
        tool_name: Tool name (for error messages).
        arguments: The arguments dict from the model.
        schema: The tool's parameters JSON Schema.

    Returns:
        Error message string if validation fails, None if valid.
    """
    if not schema or schema.get("type") != "object":
        return None

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Check required parameters
    missing = [r for r in required if r not in arguments]
    if missing:
        return (
            f"InputValidationError: Missing required parameter(s) for "
            f"tool '{tool_name}': {', '.join(missing)}"
        )

    # Check parameter types
    _JSON_TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    type_errors: list[str] = []
    for param_name, value in arguments.items():
        if param_name not in properties:
            continue  # Extra params are tolerated (flexible schema)
        prop_schema = properties[param_name]
        expected_type = prop_schema.get("type")
        if expected_type and expected_type in _JSON_TYPE_MAP:
            py_type = _JSON_TYPE_MAP[expected_type]
            if not isinstance(value, py_type):
                type_errors.append(
                    f"  - '{param_name}': expected {expected_type}, "
                    f"got {type(value).__name__}"
                )

    if type_errors:
        details = "\n".join(type_errors)
        return (
            f"InputValidationError: Invalid parameter type(s) for "
            f"tool '{tool_name}':\n{details}"
        )

    return None


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance.

        Also registers any aliases defined by the tool.

        Args:
            tool: Tool to register.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
        # Register aliases pointing to the same tool instance
        for alias in tool.aliases:
            if alias not in self._tools:
                self._tools[alias] = tool

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name.

        Args:
            name: Tool name.

        Returns:
            Tool instance or None if not found.
        """
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool]:
        """Return all registered tools (deduplicated by identity).

        Aliases are not included as separate entries — only the primary
        tool instance is returned.
        """
        seen: set[int] = set()
        result: list[BaseTool] = []
        for tool in self._tools.values():
            if id(tool) not in seen:
                seen.add(id(tool))
                result.append(tool)
        return result

    async def dispatch(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result.

        Tool execution flow:
        1. Look up tool by name
        2. Validate input against tool's JSON Schema
        3. Execute tool
        4. Return result or structured error

        Args:
            tool_call: The tool call to execute.

        Returns:
            ToolResult with the execution output or error.
        """
        tool = self.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Error: No such tool available: {tool_call.name}",
                is_error=True,
            )

        # Check if tool is enabled
        if not tool.is_enabled():
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Error: Tool '{tool_call.name}' is currently disabled.",
                is_error=True,
            )

        # Input validation
        validation_error = _validate_tool_input(
            tool.name, tool_call.arguments, tool.parameters
        )
        if validation_error:
            logger.warning(
                f"Tool '{tool_call.name}' input validation failed: "
                f"{validation_error}"
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=validation_error,
                is_error=True,
            )

        try:
            logger.debug(f"Dispatching tool '{tool_call.name}' (id={tool_call.id})")
            result = await tool.execute(**tool_call.arguments)
            logger.debug(f"Tool '{tool_call.name}' completed ({len(result)} chars)")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=result,
            )
        except Exception as e:
            logger.error(f"Tool '{tool_call.name}' execution error: {e}")
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Error executing tool '{tool_call.name}': {e}\n{traceback.format_exc()}",
                is_error=True,
            )
