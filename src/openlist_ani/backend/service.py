"""
Backend service layer.

Holds shared application state (DownloadManager, RSSManager) and provides
business logic methods used by API routes.
"""

from __future__ import annotations

from ..config import config
from ..core.download.manager import DownloadManager
from ..core.download.task import DownloadTask
from ..core.parser.model import ParseResult
from ..core.parser.parser import parse_metadata
from ..core.website.model import AnimeResourceInfo
from ..database import db
from ..logger import logger
from .schema import DownloadTaskResponse


def _build_task_response(task: DownloadTask) -> DownloadTaskResponse:
    """Convert a DownloadTask to API response model."""
    info = task.resource_info
    return DownloadTaskResponse(
        id=task.id,
        title=info.title,
        download_url=info.download_url,
        state=task.state.value,
        anime_name=info.anime_name,
        season=info.season,
        episode=info.episode,
        fansub=info.fansub,
        quality=info.quality.value if info.quality else None,
        error_message=task.error_message,
        retry_count=task.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        save_path=task.base_path,
        final_path=task.output_path,
    )


class BackendService:
    """Singleton service holding shared application state."""

    _instance: BackendService | None = None

    def __init__(self, download_manager: DownloadManager) -> None:
        self._download_manager = download_manager

    @classmethod
    def init(cls, download_manager: DownloadManager) -> BackendService:
        """Initialize the singleton service instance."""
        cls._instance = cls(download_manager)
        return cls._instance

    @classmethod
    def get(cls) -> BackendService:
        """Get the singleton service instance."""
        if cls._instance is None:
            raise RuntimeError("BackendService not initialized")
        return cls._instance

    @property
    def download_manager(self) -> DownloadManager:
        return self._download_manager

    # ── RSS ──────────────────────────────────────────────────────────

    def add_rss_url(self, url: str) -> tuple[bool, str, list[str]]:
        """Add an RSS monitoring URL.

        Returns:
            Tuple of (success, message, current_urls).
        """
        current_urls = list(config.rss.urls)
        if url in current_urls:
            return False, f"URL already exists: {url}", current_urls

        config.add_rss_url(url)
        updated_urls = list(config.rss.urls)
        logger.info(f"Added RSS URL: {url}")
        return True, f"RSS URL added successfully: {url}", updated_urls

    # ── Downloads ────────────────────────────────────────────────────

    async def create_download(
        self,
        download_url: str,
        title: str,
    ) -> tuple[bool, str, DownloadTaskResponse | None]:
        """Create a new download task.

        Returns:
            Tuple of (success, message, task_response).
        """
        # Check if already downloaded
        if await db.is_downloaded(title):
            logger.info(f"Resource already downloaded, skipping: {title}")
            return False, f"Already downloaded: {title}", None

        entry = AnimeResourceInfo(title=title, download_url=download_url)

        # Check if currently downloading
        if self._download_manager.is_downloading(entry):
            return False, f"Already downloading: {title}", None

        # Parse metadata
        try:
            parse_results = await parse_metadata([entry])
            parse_result: ParseResult = parse_results[0]
            if parse_result.success and parse_result.result:
                meta = parse_result.result
                entry.anime_name = meta.anime_name
                entry.season = meta.season
                entry.episode = meta.episode
                entry.quality = meta.quality
                entry.fansub = meta.fansub
                entry.languages = meta.languages
                entry.version = meta.version
        except Exception as e:
            logger.warning(f"Metadata parsing failed for {title}: {e}")

        # Submit download (non-blocking via public API)
        task = await self._download_manager.submit(entry, config.openlist.download_path)

        logger.info(f"Download task created: {title} (id={task.id})")
        return True, f"Download started: {title}", _build_task_response(task)

    def list_downloads(self) -> list[DownloadTaskResponse]:
        """List all active download tasks."""
        return [
            _build_task_response(task) for task in self._download_manager.list_tasks()
        ]

    def get_download(self, task_id: str) -> DownloadTaskResponse | None:
        """Get a specific download task by ID."""
        task = self._download_manager.get_task(task_id)
        if task is None:
            return None
        return _build_task_response(task)
