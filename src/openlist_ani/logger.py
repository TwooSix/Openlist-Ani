from pathlib import Path
from sys import stdout

from loguru import logger

# Configure logging path
LOG_DIR = Path.cwd() / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Remove default handler
logger.remove()


def configure_logger(
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    rotation: str = "00:00",
    retention: str = "1 week",
    log_name: str = "openlist_ani",
) -> None:
    """Configure logger with given settings.

    Args:
        console_level: Console log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        file_level: File log level
        rotation: Log rotation settings (time like "00:00" or size like "500 MB")
        retention: How long to keep old logs
        log_name: Base name for the log file
    """
    # Remove all existing handlers first
    logger.remove()

    log_file = LOG_DIR / f"{log_name}_{{time:YYYY-MM-DD}}.log"

    # Add console handler
    logger.add(
        stdout,
        level=console_level.upper(),
    )

    # Add file handler with rotation and retention
    logger.add(
        log_file,
        rotation=rotation,
        retention=retention,
        level=file_level.upper(),
        encoding="utf-8",
        mode="a",
    )


# Initialize with default settings
configure_logger()

__all__ = ["logger", "configure_logger"]
