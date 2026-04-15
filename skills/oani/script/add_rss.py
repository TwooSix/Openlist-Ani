"""Add a new RSS feed URL for monitoring."""

from openlist_ani.backend.client import BackendClient
from openlist_ani.config import config


async def run(
    url: str = "",
    **kwargs,
) -> str:
    """Add an RSS feed URL to the monitoring list.

    Args:
        url: RSS feed URL to add (required).
    """
    if not url:
        return "Error: 'url' parameter is required (RSS feed URL)."

    client = BackendClient(config.backend_url)
    try:
        data = await client.add_rss_url(url)
    except Exception as e:
        return f"Error adding RSS URL: {e}"
    finally:
        await client.close()

    success = data.get("success", False)
    msg = data.get("message", "")
    urls = data.get("urls", [])

    if success:
        return f"RSS URL added successfully.\n{msg}\nCurrent feeds ({len(urls)}):\n" + "\n".join(
            f"  - {u}" for u in urls
        )
    else:
        return f"Failed to add RSS URL: {msg}"
