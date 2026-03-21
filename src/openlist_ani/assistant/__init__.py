"""
Assistant module — Telegram chatbot that communicates with the backend via HTTP.
"""

import asyncio

from ..backend.client import BackendClient
from ..config import config
from ..database import db
from ..logger import configure_logger, logger
from .assistant import AniAssistant, StreamCallback
from .telegram_assistant import TelegramAssistant


async def run() -> None:
    """Start the assistant process."""
    configure_logger(
        level=config.log.level,
        rotation=config.log.rotation,
        retention=config.log.retention,
        log_name="assistant",
    )
    logger.info("Starting openlist-ani assistant...")

    if not config.validate():
        logger.error("Configuration validation failed. Exiting.")
        return

    if not config.assistant.enabled:
        logger.error(
            "Assistant is not enabled in config.toml. "
            "Please set [assistant] enabled = true"
        )
        return

    await db.init()
    logger.info("Database initialized")

    backend_client = BackendClient(config.backend_url)
    logger.info(f"Backend client initialized (url={config.backend_url})")

    telegram_assistant = TelegramAssistant(backend_client)

    try:
        await telegram_assistant.run()
    except KeyboardInterrupt:
        logger.info("Assistant stopped by user")
    except Exception as e:
        logger.exception(f"Assistant error: {e}")
    finally:
        # Close skill client sessions that may have been initialized
        from .skills.bangumi.script.helper.client import close_bangumi_client
        from .skills.mikan.script.helper.client import close_mikan_client

        await close_bangumi_client()
        await close_mikan_client()
        await backend_client.close()


def main() -> None:
    """Synchronous CLI entry point for the assistant process."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Assistant shutdown")


__all__ = ["AniAssistant", "StreamCallback", "TelegramAssistant", "main", "run"]
