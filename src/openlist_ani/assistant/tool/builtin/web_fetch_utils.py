"""
WebFetch utility functions — URL validation, HTTP fetch, HTML→Markdown, cache.

Pure functions + module-level cache singleton. No tool/provider dependencies.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup, NavigableString, Tag
from cachetools import TTLCache

# ── Constants ──────────────────────────────────────────────────────

MAX_URL_LENGTH = 2000
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
FETCH_TIMEOUT_SECONDS = 60
MAX_REDIRECTS = 10
MAX_MARKDOWN_LENGTH = 100_000  # chars
CACHE_MAX_SIZE = 50  # entries
CACHE_TTL_SECONDS = 900  # 15 minutes
MAX_CONTENT_FOR_SUBAGENT = 80_000  # chars sent to SubAgent

USER_AGENT = "OpenlistAni-WebFetch/1.0"
ACCEPT_HEADER = "text/markdown, text/html, */*"

# Tags to remove entirely (noise)
_NOISE_TAGS = frozenset(
    {
        "script",
        "style",
        "nav",
        "footer",
        "header",
        "aside",
        "iframe",
        "noscript",
        "svg",
        "form",
    }
)

# ── Data Classes ───────────────────────────────────────────────────


@dataclass
class FetchResult:
    """Result of an HTTP fetch operation."""

    content: str
    bytes_size: int
    status_code: int
    status_text: str
    content_type: str
    url: str


@dataclass
class RedirectInfo:
    """Returned when a cross-domain redirect is detected."""

    original_url: str
    redirect_url: str
    status_code: int


@dataclass
class CacheEntry:
    """Cached web page content."""

    content: str
    bytes_size: int
    status_code: int
    status_text: str
    content_type: str


# ── Cache ──────────────────────────────────────────────────────────

_url_cache: TTLCache[str, CacheEntry] = TTLCache(
    maxsize=CACHE_MAX_SIZE,
    ttl=CACHE_TTL_SECONDS,
)


def get_cached(url: str) -> CacheEntry | None:
    """Get cached content for a URL, or None."""
    return _url_cache.get(url)


def set_cached(url: str, entry: CacheEntry) -> None:
    """Cache content for a URL."""
    _url_cache[url] = entry


def clear_cache() -> None:
    """Clear the URL cache."""
    _url_cache.clear()


# ── URL Validation ─────────────────────────────────────────────────


def validate_url(url: str) -> tuple[bool, str]:
    """Validate URL for safety and correctness.

    Checks:
    - Non-empty
    - Parseable
    - Length <= MAX_URL_LENGTH
    - Protocol is http or https
    - No username/password in URL
    - Hostname has at least 2 segments (e.g., example.com)

    Returns:
        (is_valid, error_message) — error_message is empty when valid.
    """
    if not url:
        return False, "URL is empty."

    if len(url) > MAX_URL_LENGTH:
        return False, f"URL length ({len(url)}) exceeds maximum ({MAX_URL_LENGTH})."

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL could not be parsed."

    if parsed.scheme not in ("http", "https"):
        return (
            False,
            f"Invalid protocol '{parsed.scheme}'. Only http and https are supported.",
        )

    if not parsed.hostname:
        return False, "URL has no hostname."

    if parsed.username or parsed.password:
        return False, "URL must not contain username or password."

    parts = parsed.hostname.split(".")
    if len(parts) < 2:
        return False, f"Hostname '{parsed.hostname}' must have at least two segments."

    return True, ""


# ── Redirect Detection ─────────────────────────────────────────────


def is_same_domain_redirect(original_url: str, redirect_url: str) -> bool:
    """Check if a redirect stays on the same domain.

    Allows:
    - Adding/removing 'www.' prefix
    - Path changes on same host
    - Query parameter changes

    Blocks:
    - Protocol changes
    - Port changes
    - Different hostname (after www. normalization)
    """
    try:
        orig = urlparse(original_url)
        redir = urlparse(redirect_url)
    except Exception:
        return False

    if not orig.hostname or not redir.hostname:
        return False

    if orig.scheme != redir.scheme:
        return False

    # Compare ports (default 443 for https, 80 for http)
    def default_port(parsed) -> int:
        if parsed.port:
            return parsed.port
        return 443 if parsed.scheme == "https" else 80

    if default_port(orig) != default_port(redir):
        return False

    def strip_www(hostname: str) -> str:
        return hostname.removeprefix("www.")

    return strip_www(orig.hostname) == strip_www(redir.hostname)


# ── HTTP Fetch ─────────────────────────────────────────────────────


async def fetch_url(
    url: str,
    *,
    timeout_seconds: int = FETCH_TIMEOUT_SECONDS,
    max_content_length: int = MAX_HTTP_CONTENT_LENGTH,
) -> FetchResult | RedirectInfo:
    """Fetch URL content with aiohttp.

    - Automatically upgrades http → https
    - Follows same-domain redirects (up to MAX_REDIRECTS)
    - Returns RedirectInfo for cross-domain redirects
    - Timeout: 60 seconds
    - Max content: 10 MB
    """
    # Enforce HTTPS — all requests use encrypted transport
    parsed = urlparse(url)
    if parsed.scheme != "https":
        url = url.replace(f"{parsed.scheme}://", "https://", 1)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": ACCEPT_HEADER,
    }

    async with aiohttp.ClientSession() as session:
        current_url = url
        for _ in range(MAX_REDIRECTS):
            try:
                async with asyncio.timeout(timeout_seconds):
                    resp_ctx = session.get(
                        current_url,
                        headers=headers,
                        allow_redirects=False,
                        max_line_size=max_content_length,
                    )
                    async with resp_ctx as response:
                        # Handle redirects
                        if response.status in (301, 302, 307, 308):
                            location = response.headers.get("Location", "")
                            if not location:
                                return FetchResult(
                                    content="",
                                    bytes_size=0,
                                    status_code=response.status,
                                    status_text="Redirect missing Location header",
                                    content_type="",
                                    url=current_url,
                                )
                            # Resolve relative redirects
                            from urllib.parse import urljoin

                            redirect_url = urljoin(current_url, location)

                            if is_same_domain_redirect(current_url, redirect_url):
                                current_url = redirect_url
                                continue
                            else:
                                return RedirectInfo(
                                    original_url=url,
                                    redirect_url=redirect_url,
                                    status_code=response.status,
                                )

                        # Read content with size limit
                        raw_bytes = await response.content.read(max_content_length)
                        content = raw_bytes.decode("utf-8", errors="replace")
                        content_type = response.headers.get("Content-Type", "")

                        return FetchResult(
                            content=content,
                            bytes_size=len(raw_bytes),
                            status_code=response.status,
                            status_text=response.reason or "",
                            content_type=content_type,
                            url=current_url,
                        )
            except aiohttp.ClientError as e:
                return FetchResult(
                    content="",
                    bytes_size=0,
                    status_code=0,
                    status_text=f"Network error: {e}",
                    content_type="",
                    url=current_url,
                )

        # Exceeded MAX_REDIRECTS
        return FetchResult(
            content="",
            bytes_size=0,
            status_code=0,
            status_text=f"Too many redirects (exceeded {MAX_REDIRECTS})",
            content_type="",
            url=current_url,
        )


# ── HTML → Markdown ────────────────────────────────────────────────


def html_to_markdown(html: str) -> str:
    """Convert HTML to readable Markdown using BeautifulSoup.

    For non-HTML content (no tags detected), returns the text as-is.
    Truncates output to MAX_MARKDOWN_LENGTH characters.
    """
    # Quick check: if there are no HTML tags, return as-is
    if "<" not in html or ">" not in html:
        return html[:MAX_MARKDOWN_LENGTH]

    soup = BeautifulSoup(html, "lxml")

    # Remove noise elements
    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Convert the tree
    lines: list[str] = []
    _convert_element(soup.body or soup, lines)

    # Join and normalize whitespace
    result = "\n".join(lines)
    # Collapse 3+ consecutive blank lines into 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    if len(result) > MAX_MARKDOWN_LENGTH:
        result = result[:MAX_MARKDOWN_LENGTH] + "\n\n[Content truncated...]"

    return result


def _append_inline(lines: list[str], text: str) -> None:
    """Append text inline to the last line, or start a new one."""
    if lines and lines[-1] and not lines[-1].endswith("\n"):
        lines[-1] += " " + text
    else:
        lines.append(text)


def _convert_heading(element: Tag, lines: list[str]) -> None:
    level = int(element.name[1])
    prefix = "#" * level
    text = element.get_text(strip=True)
    lines.append("")
    lines.append(f"{prefix} {text}")
    lines.append("")


def _convert_paragraph(element: Tag, lines: list[str]) -> None:
    lines.append("")
    for child in element.children:
        _convert_element(child, lines)
    lines.append("")


def _convert_link(element: Tag, lines: list[str]) -> None:
    href = element.get("href", "")
    text = element.get_text(strip=True)
    if href and text:
        _append_inline(lines, f"[{text}]({href})")
    elif text:
        lines.append(text)


def _convert_image(element: Tag, lines: list[str]) -> None:
    alt = element.get("alt", "")
    src = element.get("src", "")
    if src:
        lines.append(f"![{alt}]({src})")


def _convert_inline_format(element: Tag, lines: list[str], wrapper: str) -> None:
    text = element.get_text(strip=True)
    if text:
        _append_inline(lines, f"{wrapper}{text}{wrapper}")


def _convert_inline_code(element: Tag, lines: list[str]) -> None:
    text = element.get_text()
    if text:
        _append_inline(lines, f"`{text}`")


def _convert_pre(element: Tag, lines: list[str]) -> None:
    code_tag = element.find("code")
    text = code_tag.get_text() if code_tag else element.get_text()
    lines.append("")
    lines.append("```")
    lines.append(text.strip())
    lines.append("```")
    lines.append("")


def _convert_blockquote(element: Tag, lines: list[str]) -> None:
    text = element.get_text(strip=True)
    lines.append("")
    for bq_line in text.split("\n"):
        lines.append(f"> {bq_line.strip()}")
    lines.append("")


def _convert_list(element: Tag, lines: list[str], ordered: bool) -> None:
    lines.append("")
    for idx, li in enumerate(element.find_all("li", recursive=False), 1):
        text = li.get_text(strip=True)
        prefix = f"{idx}." if ordered else "-"
        lines.append(f"{prefix} {text}")
    lines.append("")


def _convert_element(element: Tag | NavigableString, lines: list[str]) -> None:
    """Recursively convert a BeautifulSoup element to markdown lines."""
    if isinstance(element, NavigableString):
        text = str(element).strip()
        if text:
            _append_inline(lines, text)
        return

    if not isinstance(element, Tag):
        return

    tag = element.name

    # Heading tags
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        _convert_heading(element, lines)
        return

    # Simple tag-to-handler mapping
    simple_handlers: dict[str, Callable[[Tag, list[str]], None]] = {
        "p": _convert_paragraph,
        "a": _convert_link,
        "img": _convert_image,
        "pre": _convert_pre,
        "blockquote": _convert_blockquote,
        "table": _convert_table,
    }
    handler = simple_handlers.get(tag)
    if handler is not None:
        handler(element, lines)
        return

    # Line break / horizontal rule
    if tag == "br":
        lines.append("")
        return
    if tag == "hr":
        lines.extend(["", "---", ""])
        return

    # Inline formatting
    if tag in ("strong", "b"):
        _convert_inline_format(element, lines, "**")
        return
    if tag in ("em", "i"):
        _convert_inline_format(element, lines, "*")
        return
    if tag == "code" and element.parent and element.parent.name != "pre":
        _convert_inline_code(element, lines)
        return

    # Lists
    if tag in ("ul", "ol"):
        _convert_list(element, lines, ordered=(tag == "ol"))
        return

    # Default: recurse into children
    for child in element.children:
        _convert_element(child, lines)


def _convert_table(table: Tag, lines: list[str]) -> None:
    """Convert an HTML table to a simple Markdown table."""
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)

    if not rows:
        return

    lines.append("")
    # Header row
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    # Data rows
    for row in rows[1:]:
        # Pad row to match header length
        while len(row) < len(rows[0]):
            row.append("")
        lines.append("| " + " | ".join(row[: len(rows[0])]) + " |")
    lines.append("")
