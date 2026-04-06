"""Fetch episode releases for a specific fansub group."""

from openlist_ani.config import config
from openlist_ani.core.mikan.client import MikanClient


async def run(
    bangumi_id: str = "",
    group_id: str = "",
    **kwargs,
) -> str:
    """Fetch episode releases for a specific fansub group of a bangumi.

    Returns numbered episode entries with full title, date, and
    complete magnet link for each release.

    Args:
        bangumi_id: Mikan bangumi ID (required).
        group_id: Subtitle group ID (required).
    """
    if not bangumi_id:
        return "Error: 'bangumi_id' parameter is required."
    if not group_id:
        return "Error: 'group_id' parameter is required."

    mikan = config.mikan
    client = MikanClient(
        username=mikan.username or "",
        password=mikan.password or "",
    )
    try:
        groups = await client.fetch_bangumi_subgroups(int(bangumi_id))
    except Exception as e:
        return f"Error fetching episodes: {e}"
    finally:
        await client.close()

    # Find the matching group
    target_gid = int(group_id)
    target_group = None
    for group in groups:
        if group.get("id") == target_gid:
            target_group = group
            break

    if target_group is None:
        return (
            f"Group {group_id} not found for bangumi {bangumi_id}. "
            f"Use mikan/subgroups to list available groups."
        )

    group_name = target_group.get("name", "unknown")
    episodes = target_group.get("episodes", [])

    if not episodes:
        return f"No episodes found for group {group_name} (GroupID:{group_id})."

    lines = [
        f"Episodes from {group_name} (GroupID:{group_id}) "
        f"for bangumi {bangumi_id} ({len(episodes)} episodes):\n"
    ]
    for idx, ep in enumerate(episodes, 1):
        title = ep.get("title", "")
        date = ep.get("date", "")
        magnet = ep.get("magnet", "")
        line = f"  #{idx}. {title}"
        if date:
            line += f"  ({date})"
        lines.append(line)
        if magnet:
            lines.append(f"      Magnet: {magnet}")

    return "\n".join(lines)
