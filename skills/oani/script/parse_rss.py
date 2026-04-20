"""Parse an RSS feed and return its resource entries.

Calls the backend ``/api/parse_rss`` endpoint, which reuses the same
website parsers used by the RSS monitor.  The assistant should pick the
desired entries and submit each via ``oani/create_download``.
"""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config


async def run(
    url: str = "",
    limit: int | None = None,
    **kwargs,
) -> str:
    """Parse an RSS feed.

    Args:
        url: RSS feed URL (required).
        limit: Optional max number of entries to return.
    """
    if not url:
        return "Error: 'url' parameter is required (RSS feed URL)."

    client = BackendClient(config.backend_url)
    try:
        data = await client.parse_rss(url, limit=limit)
    except Exception as e:
        return f"Error parsing RSS: {e}"
    finally:
        await client.close()

    if not data.get("success", False):
        return f"Failed to parse RSS: {data.get('message', 'unknown error')}"

    entries = data.get("entries", [])
    total = data.get("total", len(entries))
    if not entries:
        return f"No entries parsed from {url}."

    lines = [
        f"Parsed {len(entries)} of {total} entries from {url}.",
        "",
        "| # | Title | Fansub | Quality | Lang | Magnet/Torrent |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        idx = e.get("index", "?")
        title = (e.get("title") or "").replace("|", "\\|")
        fansub = e.get("fansub") or "-"
        quality = e.get("quality") or "-"
        langs = ",".join(e.get("languages") or []) or "-"
        url_short = (e.get("download_url") or "")[:60]
        if len(e.get("download_url") or "") > 60:
            url_short += "…"
        lines.append(
            f"| {idx} | {title} | {fansub} | {quality} | {langs} | {url_short} |"
        )

    lines += [
        "",
        "Next: pick entries and call `oani/create_download(download_url, title)` "
        "for each. The `title` field above is the canonical resource title — "
        "pass it verbatim, do NOT modify it.",
    ]
    return "\n".join(lines)
