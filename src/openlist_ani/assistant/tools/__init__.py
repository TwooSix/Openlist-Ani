"""
Basic tools for the assistant agent.

These are the ONLY tools exposed as OpenAI function-calling definitions.
Domain-specific skills live under ``skills/`` and are discovered and
executed by the agent at runtime through these basic tools.
"""

from ...logger import logger
from .base import BaseTool
from .read_file import ReadFileTool
from .run_skill import RunSkillTool
from .search_file import SearchFileTool
from .send_message import MessageCallback, SendMessageTool
from .update_memory import UpdateMemoryTool
from .update_soul import UpdateSoulTool
from .update_user_profile import UpdateUserProfileTool

_TOOL_CLASSES: list[type[BaseTool]] = [
    ReadFileTool,
    SearchFileTool,
    RunSkillTool,
    SendMessageTool,
    UpdateUserProfileTool,
    UpdateMemoryTool,
    UpdateSoulTool,
]


class ToolRegistry:
    """Registry for assistant tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        for cls in _TOOL_CLASSES:
            tool = cls()
            self._tools[tool.name] = tool
        logger.info(f"ToolRegistry: {len(self._tools)} tools loaded")

    def get_tool(self, name: str) -> BaseTool | None:
        """Get a tool instance by name.

        Args:
            name: Tool name.

        Returns:
            Tool instance, or None if not found.
        """
        return self._tools.get(name)

    def get_definitions(self) -> list[dict]:
        """Get tool definitions for OpenAI function calling.

        Returns:
            List of tool definition dicts.
        """
        return [t.get_definition() for t in self._tools.values()]

    async def handle_tool_call(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool call.

        Args:
            tool_name: Tool name.
            arguments: Tool arguments.

        Returns:
            Tool execution result string.
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return f"❌ Unknown tool: {tool_name}"
        try:
            return await tool.execute(**arguments)
        except Exception as e:
            logger.exception(f"ToolRegistry: Error in {tool_name}")
            return f"❌ Tool execution error: {str(e)}"


__all__ = [
    "BaseTool",
    "MessageCallback",
    "SendMessageTool",
    "ToolRegistry",
    "UpdateMemoryTool",
    "UpdateSoulTool",
    "UpdateUserProfileTool",
]
