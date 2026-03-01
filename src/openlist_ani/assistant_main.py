"""
Entry point for assistant module.
"""

import asyncio

from .assistant import TelegramAssistant
from .assistant.tools import close_tool_clients
from .config import config
from .core.download import DownloadManager
from .core.download.downloader import OpenListDownloader
from .database import db
from .logger import configure_logger, logger


async def run_assistant() -> None:
    """Run the assistant entry point."""
    configure_logger(
        console_level=config.log.level,
        file_level=config.log.file_level,
        rotation=config.log.rotation,
        retention=config.log.retention,
        log_name="assistant",
    )
    logger.info("Starting openlist-ani assistant...")

    if not config.validate():
        logger.error("Configuration validation failed. Exiting.")
        return

    # assistant must be enabled
    if not config.assistant.enabled:
        logger.error(
            "Assistant is not enabled in config.toml. Please set [assistant] enabled = true"
        )
        return

    # Validate OpenList server health and tool availability
    if not await config.validate_openlist():
        logger.error("OpenList validation failed. Exiting.")
        return

    # Initialize database
    await db.init()
    logger.info("Database initialized")

    # Initialize downloader
    downloader = OpenListDownloader(
        base_url=config.openlist.url,
        token=config.openlist.token,
        offline_download_tool=config.openlist.offline_download_tool,
        rename_format=config.openlist.rename_format,
    )

    # Initialize download manager
    download_manager = DownloadManager(
        downloader=downloader,
        state_file="data/assistant_downloads.json",
        poll_interval=30.0,
        max_concurrent=3,
    )
    logger.info("Download manager initialized")

    # Initialize and run Telegram assistant
    telegram_assistant = TelegramAssistant(download_manager)

    try:
        await telegram_assistant.run()
    except KeyboardInterrupt:
        logger.info("Assistant stopped by user")
    except Exception as e:
        logger.exception(f"Assistant error: {e}")
    finally:
        await close_tool_clients()


def main() -> None:
    """Sync wrapper for run_assistant."""
    try:
        asyncio.run(run_assistant())
    except KeyboardInterrupt:
        logger.info("Assistant shutdown")


if __name__ == "__main__":
    main()
