"""
Mikan (mikanani.me) tools for the LLM assistant.

Provides tools for searching, subscribing to, and unsubscribing
from anime on the Mikan platform.

Helper functions (client singleton) are located in the ``helper``
subpackage.
"""

from __future__ import annotations

from typing import Any

from ...logger import logger
from .base import BaseTool
from .helper.mikan import _get_mikan_client

_MIKAN_NOT_CONFIGURED_MSG = (
    "Mikan credentials not configured. Please set "
    "[mikan] username and password in config.toml."
)


class MikanSubscribeTool(BaseTool):
    """Tool for subscribing to a bangumi on Mikan."""

    @property
    def name(self) -> str:
        return "mikan_subscribe_bangumi"

    @property
    def description(self) -> str:
        return (
            "Subscribe to an anime (bangumi) on Mikan (mikanani.me) so the "
            "user receives RSS updates for new episodes. Requires the Mikan "
            "bangumi ID (use mikan_search_bangumi to find it). "
            "Optionally specify a subtitle group NAME (e.g. 'ANi') — "
            "the tool will automatically look up the correct subtitle group "
            "ID from the bangumi page. Do NOT pass subtitle group IDs "
            "directly."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bangumi_id": {
                    "type": "integer",
                    "description": "Mikan bangumi ID",
                },
                "subtitle_group_name": {
                    "type": "string",
                    "description": (
                        "Name of the subtitle group (fansub) to subscribe "
                        "to, e.g. 'ANi'. The tool automatically "
                        "resolves the name to the correct ID. "
                        "Omit to subscribe to all groups."
                    ),
                },
                "language": {
                    "type": "integer",
                    "description": (
                        "Language filter: 0=all, 1=Simplified Chinese, "
                        "2=Traditional Chinese. Omit for all."
                    ),
                },
            },
            "required": ["bangumi_id"],
        }

    async def execute(
        self,
        bangumi_id: int,
        subtitle_group_name: str | None = None,
        language: int | None = None,
        **kwargs,
    ) -> str:
        """Execute Mikan subscribe with automatic subtitle group resolution.

        Args:
            bangumi_id: Mikan bangumi ID.
            subtitle_group_name: Optional fansub group name to resolve.
            language: Optional language filter.

        Returns:
            Success or error message.
        """
        client = _get_mikan_client()
        if client is None:
            return _MIKAN_NOT_CONFIGURED_MSG

        # Resolve subtitle group name → ID
        subtitle_group_id: int | None = None
        if subtitle_group_name:
            result = await self._resolve_subtitle_group(
                client, bangumi_id, subtitle_group_name
            )
            if isinstance(result, str):
                return result
            subtitle_group_id = result

        try:
            success = await client.subscribe_bangumi(
                bangumi_id=bangumi_id,
                subtitle_group_id=subtitle_group_id,
                language=language,
            )
        except Exception as exc:
            logger.exception(
                f"MikanSubscribeTool: Failed to subscribe bangumi {bangumi_id}"
            )
            return f"Failed to subscribe on Mikan: {exc}"

        if success:
            return self._format_success(
                bangumi_id, subtitle_group_name, subtitle_group_id, language
            )

        return (
            f"Failed to subscribe to Mikan bangumi {bangumi_id}. "
            "Check credentials or bangumi ID."
        )

    @staticmethod
    async def _resolve_subtitle_group(
        client: Any,
        bangumi_id: int,
        subtitle_group_name: str,
    ) -> int | str:
        """Resolve subtitle group name to ID.

        Args:
            client: MikanClient instance.
            bangumi_id: Mikan bangumi ID.
            subtitle_group_name: Fansub group name.

        Returns:
            Subtitle group ID on success, or error message string on failure.
        """
        try:
            subgroups = await client.fetch_bangumi_subgroups(bangumi_id)
        except Exception as exc:
            logger.warning(
                f"MikanSubscribeTool: Failed to fetch subgroups "
                f"for bangumi {bangumi_id}: {exc}"
            )
            subgroups = []

        if not subgroups:
            return (
                f"No subtitle groups found for bangumi {bangumi_id}. "
                f"Cannot resolve '{subtitle_group_name}'."
            )

        # Exact match first, then substring match
        matched = next(
            (g for g in subgroups if g["name"] == subtitle_group_name),
            None,
        )
        if matched is None:
            matched = next(
                (
                    g
                    for g in subgroups
                    if subtitle_group_name in g["name"]
                    or g["name"] in subtitle_group_name
                ),
                None,
            )
        if matched:
            logger.info(
                f"MikanSubscribeTool: Resolved '{subtitle_group_name}'"
                f" → subgroup ID {matched['id']} ({matched['name']})"
            )
            return matched["id"]

        available = ", ".join(f"{g['name']}(ID:{g['id']})" for g in subgroups)
        return (
            f"Subtitle group '{subtitle_group_name}' not found "
            f"for bangumi {bangumi_id}. "
            f"Available groups: {available}"
        )

    @staticmethod
    def _format_success(
        bangumi_id: int,
        subtitle_group_name: str | None,
        subtitle_group_id: int | None,
        language: int | None,
    ) -> str:
        """Format a success message for subscription.

        Args:
            bangumi_id: Mikan bangumi ID.
            subtitle_group_name: Resolved fansub group name.
            subtitle_group_id: Resolved fansub group ID.
            language: Language filter.

        Returns:
            Formatted success message.
        """
        parts = [f"Successfully subscribed to Mikan bangumi {bangumi_id}"]
        if subtitle_group_id is not None:
            parts.append(
                f"subtitle group: {subtitle_group_name} (ID: {subtitle_group_id})"
            )
        lang_labels = {0: "all", 1: "Simplified Chinese", 2: "Traditional Chinese"}
        if language is not None:
            parts.append(f"language: {lang_labels.get(language, str(language))}")
        return " | ".join(parts)


class MikanUnsubscribeTool(BaseTool):
    """Tool for unsubscribing from a bangumi on Mikan."""

    @property
    def name(self) -> str:
        return "mikan_unsubscribe_bangumi"

    @property
    def description(self) -> str:
        return (
            "Unsubscribe from an anime (bangumi) on Mikan (mikanani.me). "
            "Requires the Mikan bangumi ID."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bangumi_id": {
                    "type": "integer",
                    "description": "Mikan bangumi ID",
                },
                "subtitle_group_id": {
                    "type": "integer",
                    "description": (
                        "Optional subtitle group (fansub) ID. "
                        "Omit to unsubscribe from all groups."
                    ),
                },
            },
            "required": ["bangumi_id"],
        }

    async def execute(
        self,
        bangumi_id: int,
        subtitle_group_id: int | None = None,
        **kwargs,
    ) -> str:
        """Execute Mikan unsubscribe.

        Args:
            bangumi_id: Mikan bangumi ID.
            subtitle_group_id: Optional fansub group ID.

        Returns:
            Success or error message.
        """
        client = _get_mikan_client()
        if client is None:
            return _MIKAN_NOT_CONFIGURED_MSG

        try:
            success = await client.unsubscribe_bangumi(
                bangumi_id=bangumi_id,
                subtitle_group_id=subtitle_group_id,
            )
        except Exception as exc:
            logger.exception(
                f"MikanUnsubscribeTool: Failed to unsubscribe bangumi {bangumi_id}"
            )
            return f"Failed to unsubscribe on Mikan: {exc}"

        if success:
            return f"Successfully unsubscribed from Mikan bangumi {bangumi_id}"

        return (
            f"Failed to unsubscribe from Mikan bangumi {bangumi_id}. "
            "Check credentials or bangumi ID."
        )


class MikanSearchTool(BaseTool):
    """Tool for searching bangumi on Mikan."""

    @property
    def name(self) -> str:
        return "mikan_search_bangumi"

    @property
    def description(self) -> str:
        return (
            "Search for anime on Mikan (mikanani.me) by keyword. "
            "Returns a list of matching anime with their IDs. Use this "
            "to find the Mikan anime ID before subscribing."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (anime name)",
                },
            },
            "required": ["keyword"],
        }

    async def execute(self, keyword: str, **kwargs) -> str:
        """Execute Mikan search.

        Args:
            keyword: Search keyword.

        Returns:
            Formatted search results.
        """
        client = _get_mikan_client()
        if client is None:
            return _MIKAN_NOT_CONFIGURED_MSG

        try:
            results = await client.search_bangumi(keyword)
        except Exception as exc:
            logger.exception(f"MikanSearchTool: Failed to search for '{keyword}'")
            return f"Failed to search Mikan: {exc}"

        if not results:
            return f"No results found on Mikan for '{keyword}'."

        lines = [f"Mikan Search Results for '{keyword}' ({len(results)} found)\n"]
        for item in results[:20]:
            lines.append(
                f"  - [ID:{item['bangumi_id']}] {item['name']} ({item['url']})"
            )
        if len(results) > 20:
            lines.append(f"\n  ...and {len(results) - 20} more results")

        return "\n".join(lines)
