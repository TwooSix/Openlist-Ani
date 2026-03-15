from pathlib import Path
from sys import stdout

from loguru import logger

# Configure logging path
LOG_DIR = Path.cwd() / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Remove default handler
logger.remove()


def configure_logger(
    level: str = "INFO",
    rotation: str = "00:00",
    retention: str = "1 week",
    log_name: str = "openlist_ani",
) -> None:
    """Configure logger with given settings.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
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
        level=level.upper(),
    )

    # Add file handler with rotation and retention
    logger.add(
        log_file,
        rotation=rotation,
        retention=retention,
        level=level.upper(),
        encoding="utf-8",
        mode="a",
    )


# Initialize with default settings
configure_logger()

__all__ = ["logger", "configure_logger"]
