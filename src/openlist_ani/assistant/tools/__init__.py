"""
Assistant tools module.

Provides tool classes for assistant function calling.
"""

from ...core.download import DownloadManager
from ...logger import logger
from .bangumi_tool import (
    BangumiCalendarTool,
    BangumiCollectionTool,
    BangumiCollectTool,
    BangumiRecommendTool,
    BangumiReviewsTool,
    BangumiSubjectTool,
)
from .base import BaseTool
from .db_tool import ExecuteSqlTool
from .download_tool import DownloadResourceTool
from .helper.bangumi import close_bangumi_client
from .helper.mikan import close_mikan_client
from .mikan_tool import (
    MikanSearchTool,
    MikanSubscribeTool,
    MikanUnsubscribeTool,
)
from .parse_rss import ParseRssTool
from .search_anime import SearchAnimeTool

# Registry of all available tools
_kToolClasses: list[type[BaseTool]] = [
    SearchAnimeTool,
    ParseRssTool,
    DownloadResourceTool,
    ExecuteSqlTool,
    BangumiCalendarTool,
    BangumiSubjectTool,
    BangumiCollectionTool,
    BangumiReviewsTool,
    BangumiCollectTool,
    BangumiRecommendTool,
    MikanSearchTool,
    MikanSubscribeTool,
    MikanUnsubscribeTool,
]


class ToolRegistry:
    """Registry for managing assistant tools."""

    def __init__(self, download_manager: DownloadManager | None = None):
        """Initialize tool registry.

        Args:
            download_manager: DownloadManager instance for download tool
        """
        self._tools: dict[str, BaseTool] = {}
        self._download_manager = download_manager
        self._init_tools()

    def _init_tools(self) -> None:
        """Initialize all tool instances."""
        for tool_cls in _kToolClasses:
            if tool_cls == DownloadResourceTool:
                tool = tool_cls(self._download_manager)
            else:
                tool = tool_cls()
            self._tools[tool.name] = tool

    def set_download_manager(self, download_manager: DownloadManager) -> None:
        """Set download manager for download tool.

        Args:
            download_manager: DownloadManager instance
        """
        self._download_manager = download_manager
        if "download_resource" in self._tools:
            download_tool = self._tools["download_resource"]
            if isinstance(download_tool, DownloadResourceTool):
                download_tool.download_manager = download_manager

    def get_tool(self, name: str) -> BaseTool | None:
        """Get tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        return self._tools.get(name)

    def get_definitions(self) -> list[dict]:
        """Get all tool definitions for OpenAI function calling.

        Returns:
            List of tool definition dictionaries
        """
        return [tool.get_definition() for tool in self._tools.values()]

    async def handle_tool_call(self, tool_name: str, arguments: dict) -> str:
        """Handle tool call from assistant.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool execution result as string
        """
        tool = self.get_tool(tool_name)
        if not tool:
            return f"❌ Unknown tool: {tool_name}"

        try:
            return await tool.execute(**arguments)
        except Exception as e:
            logger.exception(f"Assistant: Error handling tool call {tool_name}")
            return f"❌ Tool execution error: {str(e)}"


# Convenience functions for backward compatibility
_default_registry: ToolRegistry | None = None


def get_registry(
    download_manager: DownloadManager | None = None,
) -> ToolRegistry:
    """Get or create the default tool registry.

    Args:
        download_manager: DownloadManager instance

    Returns:
        ToolRegistry instance
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry(download_manager)
    elif download_manager is not None:
        _default_registry.set_download_manager(download_manager)
    return _default_registry


def get_assistant_tools() -> list[dict]:
    """Get tool definitions for OpenAI function calling.

    Returns:
        List of tool definition dictionaries
    """
    return get_registry().get_definitions()


async def handle_tool_call(
    tool_name: str, arguments: dict, download_manager: DownloadManager
) -> str:
    """Handle tool call from assistant.

    Args:
        tool_name: Name of the tool to call
        arguments: Tool arguments
        download_manager: DownloadManager instance

    Returns:
        Tool execution result as string
    """
    registry = get_registry(download_manager)
    return await registry.handle_tool_call(tool_name, arguments)


async def close_tool_clients() -> None:
    """Close shared HTTP clients used by tool modules."""
    try:
        await close_bangumi_client()
    except Exception as exc:
        logger.warning(f"Failed to close Bangumi client cleanly: {exc}")

    try:
        await close_mikan_client()
    except Exception as exc:
        logger.warning(f"Failed to close Mikan client cleanly: {exc}")


__all__ = [
    "BaseTool",
    "SearchAnimeTool",
    "ParseRssTool",
    "DownloadResourceTool",
    "ExecuteSqlTool",
    "BangumiCalendarTool",
    "BangumiSubjectTool",
    "BangumiCollectionTool",
    "BangumiReviewsTool",
    "BangumiCollectTool",
    "BangumiRecommendTool",
    "MikanSearchTool",
    "MikanSubscribeTool",
    "MikanUnsubscribeTool",
    "ToolRegistry",
    "get_registry",
    "get_assistant_tools",
    "handle_tool_call",
    "close_tool_clients",
]
