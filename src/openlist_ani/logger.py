import ast
from functools import lru_cache
from pathlib import Path
import re
from sys import stdout
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from loguru import logger as _logger

# Configure logging path
LOG_DIR = Path.cwd() / "logs"
LOG_DIR.mkdir(exist_ok=True)

FATAL_LEVEL = "FATAL"
LOG_FORMAT = (
    "{time:HH:mm:ss.SSS} | {level: <8} | "
    "{extra[source_location]} | [{extra[tag]}] {message}"
)
CONSOLE_LOG_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan><u>{extra[source_location]}</u></cyan> | "
    "<level><magenta>[{extra[tag]}]</magenta> {message}</level>"
)

_logger.level(FATAL_LEVEL, no=50, color="<red><bold>")

_URL_PATTERN = re.compile(r"https?://[^\s'\"<>()\]]+")
_MAGNET_PATTERN = re.compile(r"magnet:\?[^\s'\"<>()\]]+")
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|passwd|secret|sign|signature|passkey)=([^&\s,;]+)"
)
_TELEGRAM_BOT_PATTERN = re.compile(r"(api\.telegram\.org/bot)[^/\s'\"<>()\]]+")
_PUSHPLUS_TOKEN_PATTERN = re.compile(r"(pushplus\.plus/send/)[^/\s'\"<>()\]]+")


def sanitize_for_log(value: object) -> str:
    text = str(value)
    text = _MAGNET_PATTERN.sub(_sanitize_magnet_url, text)
    text = _URL_PATTERN.sub(_sanitize_url_match, text)
    text = _SENSITIVE_KEY_PATTERN.sub(r"\1=<redacted>", text)
    return text


def _sanitize_magnet_url(match: re.Match[str]) -> str:
    magnet = match.group(0)
    xt_match = re.search(r"xt=urn:btih:([^&]+)", magnet, flags=re.IGNORECASE)
    if xt_match:
        info_hash = xt_match.group(1)
        return f"magnet:?xt=urn:btih:{info_hash[:12]}..."
    return "magnet:?<redacted>"


def _sanitize_url_match(match: re.Match[str]) -> str:
    url = match.group(0).rstrip(".,;")
    suffix = match.group(0)[len(url) :]
    return f"{_sanitize_url(url)}{suffix}"


def _sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _SENSITIVE_KEY_PATTERN.sub(r"\1=<redacted>", url)

    hostname = parts.hostname or ""
    netloc = hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"

    query = ""
    if parts.query:
        query = urlencode([(key, "<redacted>") for key, _ in parse_qsl(parts.query)])

    sanitized = urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    sanitized = _TELEGRAM_BOT_PATTERN.sub(r"\1<redacted>", sanitized)
    sanitized = _PUSHPLUS_TOKEN_PATTERN.sub(r"\1<redacted>", sanitized)
    return sanitized


def _sanitize_record(record: dict) -> None:
    record["extra"]["source_location"] = _source_location(record)
    if not record["extra"].get("tag"):
        record["extra"]["tag"] = _auto_tag(record)
    record["message"] = sanitize_for_log(record["message"])


def _source_location(record: dict) -> str:
    file_path = Path(str(record.get("file").path))
    try:
        source_path = file_path.resolve().relative_to(Path.cwd().resolve())
    except (OSError, ValueError):
        source_path = file_path
    return f"{source_path.as_posix()}:{record.get('line')}"


def _auto_tag(record: dict) -> str:
    file_path = Path(str(record.get("file").path))
    line = int(record.get("line") or 0)
    class_name = _class_name_at_line(file_path, line)
    if class_name:
        return class_name
    return str(record.get("module") or file_path.stem)


def _class_name_at_line(file_path: Path, line: int) -> str | None:
    for start, end, name in _class_ranges(str(file_path)):
        if start <= line <= end:
            return name
    return None


@lru_cache(maxsize=512)
def _class_ranges(file_path: str) -> tuple[tuple[int, int, str], ...]:
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return ()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()

    ranges: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.end_lineno is not None:
            ranges.append((node.lineno, node.end_lineno, node.name))
    ranges.sort(key=lambda item: (item[1] - item[0], item[0]))
    return tuple(ranges)


logger = _logger.patch(_sanitize_record)

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
        format=CONSOLE_LOG_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # Add file handler with rotation and retention
    logger.add(
        log_file,
        rotation=rotation,
        retention=retention,
        level=level.upper(),
        encoding="utf-8",
        mode="a",
        format=LOG_FORMAT,
        backtrace=False,
        diagnose=False,
    )


# Initialize with default settings
configure_logger()

__all__ = [
    "CONSOLE_LOG_FORMAT",
    "FATAL_LEVEL",
    "LOG_FORMAT",
    "configure_logger",
    "logger",
    "sanitize_for_log",
]
