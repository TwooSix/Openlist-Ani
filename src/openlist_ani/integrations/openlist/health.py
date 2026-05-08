"""OpenList runtime health checks."""

from __future__ import annotations

from typing import Any

from openlist_ani.logger import logger

from .client import OpenListClient
from .model import OfflineDownloadTool, normalize_offline_download_tool_name


class OpenListHealthCheck:
    """Validate OpenList reachability and configured offline-download support."""

    def __init__(
        self,
        client: OpenListClient,
        base_url: str,
        offline_download_tool: OfflineDownloadTool | str,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._offline_download_tool = offline_download_tool

    async def validate(self) -> bool:
        logger.debug("Verifying OpenList server health")
        if not await self._client.is_healthy():
            logger.warning(
                f"Cannot connect to OpenList server at {self._base_url}. "
                "Please check that the server is running and the URL is correct."
            )
            return False
        logger.debug("OpenList server health check OK")

        tool_str = normalize_offline_download_tool_name(self._offline_download_tool)
        logger.debug(f"Verifying offline download tool '{tool_str}'")
        available_tools = await self._client.get_offline_download_tools()
        if available_tools is None:
            logger.warning("Failed to retrieve offline download tools from server")
            return False

        available_names = self._tool_names(available_tools)
        available_by_casefold = {name.casefold(): name for name in available_names}
        if tool_str.casefold() not in available_by_casefold:
            logger.warning(
                f"The configured offline download tool '{tool_str}' is not "
                f"available on the server. Available tools: {available_names}. "
                "Please check [openlist] offline_download_tool in config.toml."
            )
            return False

        logger.debug(
            "Offline download tool "
            f"'{available_by_casefold[tool_str.casefold()]}' is available"
        )
        return True

    @staticmethod
    def _tool_names(available_tools: list[dict[str, Any]] | list[str]) -> list[str]:
        return [
            tool.get("name", "") if isinstance(tool, dict) else str(tool)
            for tool in available_tools
        ]
