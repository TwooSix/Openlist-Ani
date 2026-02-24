import asyncio
import sys

from .config import config
from .core.download import DownloadManager, OpenListDownloader
from .core.notification.manager import NotificationManager
from .core.rss import RSSManager
from .core.website.model import AnimeResourceInfo
from .database import db
from .logger import configure_logger, logger
from .worker import download_dispatch_worker, rss_poll_worker


async def run():
    """Main application entry point."""
    # Configure logger from config
    configure_logger(
        console_level=config.log.level,
        file_level=config.log.file_level,
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
    logger.info("=" * 60)

    await db.init()

    # Create single DownloadManager instance with concurrency control
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

    # Register callback to save completed downloads to database
    async def save_to_database(task):
        """Save completed download to database."""
        try:
            await db.add_resource(task.resource_info)
            logger.info(f"Saved to database: {task.resource_info.title}")
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")

    manager.on_complete(save_to_database)

    # Register callback to send notification on download completion
    notification_manager = NotificationManager.from_config(config.notification)
    if notification_manager:
        # Start the batch notification worker
        notification_manager.start()

        async def send_notification(task):
            """Send notification when download completes."""
            try:
                anime_name = task.resource_info.anime_name or "Unknown"
                title = task.resource_info.title
                await notification_manager.send_download_complete_notification(
                    anime_name, title
                )
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")

        manager.on_complete(send_notification)

    # Create RSS manager with reference to download manager
    rss = RSSManager(download_manager=manager)
    rss_entry_queue: asyncio.Queue[AnimeResourceInfo] = asyncio.Queue()
    active_downloads: set[asyncio.Task[None]] = set()

    poll_task = asyncio.create_task(rss_poll_worker(rss, rss_entry_queue))
    dispatch_task = asyncio.create_task(
        download_dispatch_worker(manager, rss_entry_queue, active_downloads)
    )

    try:
        await asyncio.gather(poll_task, dispatch_task)
    except asyncio.CancelledError:
        logger.info("Shutting down...")
        poll_task.cancel()
        dispatch_task.cancel()
        for task in tuple(active_downloads):
            task.cancel()
        await asyncio.gather(
            poll_task,
            dispatch_task,
            *active_downloads,
            return_exceptions=True,
        )
        raise
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    finally:
        # Stop notification manager and send any pending notifications
        if notification_manager:
            await notification_manager.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
