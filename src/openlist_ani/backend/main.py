"""
Backend process entry point.

Orchestrates the full application lifecycle:
- Database initialization
- DownloadManager setup with callbacks
- Notification manager
- RSS polling and download dispatch workers
- FastAPI API server (uvicorn)

All components share a single event loop and DownloadManager instance.
"""

import asyncio
import sys

import uvicorn

from ..config import config
from ..core.download import DownloadManager, OpenListDownloader
from ..core.download.task import DownloadTask
from ..core.notification.manager import NotificationManager
from ..core.rss import RSSManager
from ..core.website.model import AnimeResourceInfo
from ..database import db
from ..logger import configure_logger, logger
from .app import create_app
from .service import BackendService
from .worker import dispatch_downloads, poll_rss_feeds


async def run() -> None:
    """Start the backend process: API server + background workers."""
    configure_logger(
        level=config.log.level,
        rotation=config.log.rotation,
        retention=config.log.retention,
        log_name="openlist_ani",
    )

    if not config.validate():
        logger.error("Configuration validation failed. Exiting.")
        sys.exit(1)

    if not await config.validate_openlist():
        logger.error("OpenList validation failed. Exiting.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("OpenList Anime Downloader Starting...")
    logger.info(f"RSS Sources: {len(config.rss.urls)} configured")
    logger.info(f"Download Path: {config.openlist.download_path}")
    logger.info(f"LLM Model: {config.llm.openai_model}")
    logger.info(f"Backend API: {config.backend_url}")
    logger.info("=" * 60)

    await db.init()

    manager = _create_download_manager()
    BackendService.init(manager)
    notification_manager = _setup_notifications(manager)

    rss = RSSManager()
    rss_entry_queue: asyncio.Queue[AnimeResourceInfo] = asyncio.Queue()
    active_downloads: set[asyncio.Task[None]] = set()

    poll_task = asyncio.create_task(poll_rss_feeds(rss, rss_entry_queue))
    dispatch_task = asyncio.create_task(
        dispatch_downloads(manager, rss_entry_queue, active_downloads)
    )

    server = _create_api_server()
    api_task = asyncio.create_task(server.serve())
    logger.info(
        f"Backend API server listening on {config.backend.host}:{config.backend.port}"
    )

    try:
        await asyncio.gather(poll_task, dispatch_task, api_task)
    except asyncio.CancelledError:
        logger.info("Shutting down...")
        poll_task.cancel()
        dispatch_task.cancel()
        server.should_exit = True
        for task in tuple(active_downloads):
            task.cancel()
        await asyncio.gather(
            poll_task,
            dispatch_task,
            api_task,
            *active_downloads,
            return_exceptions=True,
        )
        raise
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        if notification_manager:
            await notification_manager.stop()


def _create_download_manager() -> DownloadManager:
    """Build and return the shared DownloadManager with DB-save callback."""
    manager = DownloadManager(
        OpenListDownloader(
            base_url=config.openlist.url,
            token=config.openlist.token,
            offline_download_tool=config.openlist.offline_download_tool,
            rename_format=config.openlist.rename_format,
        ),
        state_file="data/pending_downloads.json",
        max_concurrent=3,
    )

    async def _save_to_database(task: DownloadTask) -> None:
        try:
            await db.add_resource(task.resource_info)
            logger.info(f"Saved to database: {task.resource_info.title}")
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")

    async def _rollback_database(task: DownloadTask, error: str) -> None:
        try:
            await db.remove_resource(task.resource_info.title)
            logger.info(
                f"Rolled back DB record for failed download: {task.resource_info.title}"
            )
        except Exception as e:
            logger.error(f"Failed to rollback DB record: {e}")

    manager.on_complete(_save_to_database)
    manager.on_error(_rollback_database)
    return manager


def _setup_notifications(manager: DownloadManager) -> NotificationManager | None:
    """Wire up notification callbacks if configured."""
    notification_manager = NotificationManager.from_config(config.notification)
    if not notification_manager:
        return None

    notification_manager.start()

    async def _send_notification(task: DownloadTask) -> None:
        try:
            anime_name = task.resource_info.anime_name or "Unknown"
            title = task.resource_info.title
            await notification_manager.send_download_complete_notification(
                anime_name, title
            )
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    manager.on_complete(_send_notification)
    return notification_manager


def _create_api_server() -> uvicorn.Server:
    """Create the uvicorn server instance for the FastAPI app."""
    fastapi_app = create_app()
    uvicorn_config = uvicorn.Config(
        fastapi_app,
        host=config.backend.host,
        port=config.backend.port,
        log_level="warning",
    )
    return uvicorn.Server(uvicorn_config)


def main() -> None:
    """Synchronous CLI entry point for the backend process."""
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
