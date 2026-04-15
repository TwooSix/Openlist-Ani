"""Search anime/manga on Bangumi by keyword."""

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient


async def run(
    keyword: str = "",
    sort: str = "match",
    subject_type: str = "2",
    limit: str = "10",
    **kwargs,
) -> str:
    """Search Bangumi subjects by keyword.

    Args:
        keyword: Search keyword (required).
        sort: Sort order — "match", "heat", "rank", or "score". Default "match".
        subject_type: Subject type filter. 2=anime, 1=book, 4=game. Default "2" (anime).
        limit: Max results to return. Default "10".
    """
    if not keyword:
        return "Error: 'keyword' parameter is required."

    client = BangumiClient(access_token=config.bangumi_token)
    try:
        type_list = [int(subject_type)] if subject_type else None
        data = await client.search_subjects(
            keyword=keyword,
            sort=sort,
            subject_type=type_list,
            limit=int(limit),
        )
    except Exception as e:
        return f"Error searching Bangumi: {e}"
    finally:
        await client.close()

    total = data.get("total", 0)
    items = data.get("data", [])
    if not items:
        return f"No results found for '{keyword}'."

    lines = [f"Search results for '{keyword}' (total: {total}):\n"]
    for item in items:
        sid = item.get("id", "")
        name = item.get("name", "")
        name_cn = item.get("name_cn", "")
        score = item.get("score", 0)
        date = item.get("date", "")
        rank = item.get("rank", 0)

        display = name_cn or name
        if name_cn and name:
            display = f"{name_cn} ({name})"

        line = f"- [ID:{sid}] {display}"
        if score:
            line += f"  score:{score}"
        if rank:
            line += f"  rank:#{rank}"
        if date:
            line += f"  date:{date}"
        lines.append(line)

    return "\n".join(lines)
