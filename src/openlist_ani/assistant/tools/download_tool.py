"""
Download resource tool.
"""

from typing import Any, Dict, Optional

from ...config import config
from ...core.download import DownloadManager
from ...core.parser.parser import parse_metadata
from ...core.website.model import AnimeResourceInfo
from ...database import db
from ...logger import logger
from .base import BaseTool


class DownloadResourceTool(BaseTool):
    """Tool for downloading anime resources."""

    def __init__(self, download_manager: Optional[DownloadManager] = None):
        """Initialize with optional download manager.

        Args:
            download_manager: DownloadManager instance (can be set later)
        """
        self._download_manager = download_manager

    @property
    def download_manager(self) -> DownloadManager:
        """Get download manager, raising error if not set."""
        if self._download_manager is None:
            raise RuntimeError("DownloadManager not set")
        return self._download_manager

    @download_manager.setter
    def download_manager(self, value: DownloadManager):
        """Set download manager."""
        self._download_manager = value

    @property
    def name(self) -> str:
        return "download_resource"

    @property
    def description(self) -> str:
        return (
            "Download a single anime resource using its download URL (magnet/torrent link). "
            "Parses metadata, checks download history, and starts download. "
            "Blocks until download completes or fails. "
            "Requires a URL from search_anime_resources or parse_rss results. "
            "NEVER download resources already marked as downloaded."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
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
        """Download a single anime resource.

        Args:
            download_url: Download URL (magnet/torrent)
            title: Resource title

        Returns:
            Result message
        """
        logger.info(f"Assistant: Attempting to download resource: {title}")
        logger.info(f"Assistant: Download URL: {download_url}")

        try:
            # Check if already downloaded by title
            is_downloaded = await db.is_downloaded(title)
            logger.info(f"Assistant: Title '{title}' download status: {is_downloaded}")

            if is_downloaded:
                logger.warning(
                    f"Assistant: Resource already downloaded, skipping: {title}"
                )
                return f"✅ Already downloaded (skipped): {title}"

            # Create a minimal AnimeResourceInfo object
            entry = AnimeResourceInfo(
                title=title,
                download_url=download_url,
            )

            # Check if currently processing
            if self.download_manager.is_downloading(entry):
                return f"⏳ Already downloading: {title}"

            # Parse metadata
            try:
                parse_results = await parse_metadata([entry])
                parse_result = parse_results[0]
                meta = parse_result.result if parse_result.success else None
                if meta:
                    entry.anime_name = meta.anime_name
                    entry.season = meta.season
                    entry.episode = meta.episode
                    entry.quality = meta.quality
                    entry.fansub = meta.fansub
                    entry.languages = meta.languages
                    entry.version = meta.version

                # Execute download (this blocks until download completes)
                success = await self.download_manager.download(
                    entry, config.openlist.download_path
                )

                if success:
                    # Download completed successfully, insert into database
                    await db.add_resource(entry)
                    result_msg = f"✅ Download completed: {title}"
                    if meta and meta.season is not None and meta.episode is not None:
                        result_msg += f" ({meta.anime_name} S{meta.season:02d}E{meta.episode:02d})"
                    logger.info(
                        f"Assistant: Successfully downloaded and recorded {title}"
                    )
                    return result_msg
                else:
                    # Download failed
                    logger.warning(f"Assistant: Download failed for {title}")
                    return f"❌ Download failed: {title}"

            except Exception as e:
                logger.exception(f"Assistant: Error processing resource {title}")
                return f"❌ Failed to download {title}: {str(e)}"

        except Exception as e:
            logger.exception("Assistant: Error downloading resource")
            return f"❌ Error: {str(e)}"
