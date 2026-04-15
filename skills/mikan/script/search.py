"""Search anime on Mikan by keyword."""

from openlist_ani.config import config
from openlist_ani.core.mikan.client import MikanClient


async def run(
    keyword: str = "",
    **kwargs,
) -> str:
    """Search for anime on Mikan (mikanani.me).

    Args:
        keyword: Search keyword (required).
    """
    if not keyword:
        return "Error: 'keyword' parameter is required."

    mikan = config.mikan
    client = MikanClient(
        username=mikan.username or "",
        password=mikan.password or "",
    )
    try:
        results = await client.search_bangumi(keyword)
    except Exception as e:
        return f"Error searching Mikan: {e}"
    finally:
        await client.close()

    if not results:
        return f"No results found for '{keyword}'."

    lines = [f"Mikan search results for '{keyword}' ({len(results)} found):\n"]
    for item in results:
        bid = item.get("bangumi_id", "")
        name = item.get("name", "")
        url = item.get("url", "")
        lines.append(f"  - [BangumiID:{bid}] {name}")
        if url:
            lines.append(f"    URL: {url}")

    return "\n".join(lines)
