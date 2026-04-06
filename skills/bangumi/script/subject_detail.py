"""Get detailed info for a Bangumi subject."""

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient


async def run(
    subject_id: str = "",
    **kwargs,
) -> str:
    """Fetch full details for a Bangumi subject (anime/manga/game).

    Args:
        subject_id: Bangumi subject ID (required).
    """
    if not subject_id:
        return "Error: 'subject_id' parameter is required."

    client = BangumiClient(access_token=config.bangumi_token)
    try:
        subject = await client.fetch_subject(int(subject_id))
    except Exception as e:
        return f"Error fetching subject {subject_id}: {e}"
    finally:
        await client.close()

    lines = []
    display = subject.display_name
    if subject.name_cn and subject.name:
        display = f"{subject.name_cn} ({subject.name})"

    lines.append(f"# {display}")
    lines.append(f"ID: {subject.id}")
    lines.append(f"Type: {subject.type}")

    if subject.date:
        lines.append(f"Air date: {subject.date}")
    if subject.eps:
        lines.append(f"Episodes: {subject.eps}")
    if subject.platform:
        lines.append(f"Platform: {subject.platform}")

    if subject.rating:
        r = subject.rating
        lines.append(f"Rating: {r.score}/10 ({r.total} votes, rank #{r.rank})")

    if subject.summary:
        summary = subject.summary
        if len(summary) > 500:
            summary = summary[:500] + "..."
        lines.append(f"\nSummary:\n{summary}")

    if subject.tags:
        tag_strs = [f"{t.name}({t.count})" for t in subject.tags[:15]]
        lines.append(f"\nTags: {', '.join(tag_strs)}")

    return "\n".join(lines)
