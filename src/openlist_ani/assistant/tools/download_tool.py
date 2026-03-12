"""
Download resource tool.
"""

from typing import Any

from ...backend.client import BackendClient
from ...database import db
from ...logger import logger
from .base import BaseTool


class DownloadResourceTool(BaseTool):
    """Tool for downloading anime resources via the backend API."""

    def __init__(self, backend_client: BackendClient | None = None):
        """Initialize with optional backend client.

        Args:
            backend_client: BackendClient instance (can be set later)
        """
        self._backend_client = backend_client

    @property
    def backend_client(self) -> BackendClient:
        """Get backend client, raising error if not set."""
        if self._backend_client is None:
            raise RuntimeError("BackendClient not set")
        return self._backend_client

    @backend_client.setter
    def backend_client(self, value: BackendClient) -> None:
        """Set backend client."""
        self._backend_client = value

    @property
    def name(self) -> str:
        return "download_resource"

    @property
    def description(self) -> str:
        return (
            "Download a single anime resource using its download URL (magnet/torrent link). "
            "Parses metadata, checks download history, and starts download. "
            "Returns immediately after submitting the task; use get_download_status to track progress. "
            "Requires a URL from search_anime_resources or parse_rss results. "
            "NEVER download resources already marked as downloaded."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "download_url": {
                    "type": "string",
                    "description": "The download URL (magnet link or torrent URL)",
                },
                "title": {
                    "type": "string",
                    "description": "Resource title for identification",
                },
            },
            "required": ["download_url", "title"],
        }

    async def execute(self, download_url: str, title: str, **kwargs) -> str:
        """Download a single anime resource via the backend API.

        Args:
            download_url: Download URL (magnet/torrent)
            title: Resource title

        Returns:
            Result message
        """
        logger.info(f"Assistant: Attempting to download resource: {title}")

        try:
            # Check if already downloaded by title
            is_downloaded = await db.is_downloaded(title)
            if is_downloaded:
                logger.warning(
                    f"Assistant: Resource already downloaded, skipping: {title}"
                )
                return f"✅ Already downloaded (skipped): {title}"

            # Delegate to backend API
            result = await self.backend_client.create_download(
                download_url=download_url,
                title=title,
            )

            if result.get("success"):
                task = result.get("task", {})
                msg = f"✅ Download submitted: {title}"
                anime_name = task.get("anime_name")
                season = task.get("season")
                episode = task.get("episode")
                if anime_name and season is not None and episode is not None:
                    msg += f" ({anime_name} S{season:02d}E{episode:02d})"
                logger.info(f"Assistant: Download task created for {title}")
                return msg

            message = result.get("message", "Unknown error")
            logger.warning(f"Assistant: Download rejected: {message}")
            return f"⚠️ {message}"

        except Exception as e:
            logger.exception("Assistant: Error creating download task")
            return f"❌ Error: {str(e)}"
