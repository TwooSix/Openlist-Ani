"""Fetch releases for a specific fansub group."""

from openlist_ani.config import config
from openlist_ani.core.mikan.client import MikanClient


async def run(
    bangumi_id: str = "",
    group_id: str = "",
    **kwargs,
) -> str:
    """Fetch releases for a specific fansub group of a bangumi.

    Returns numbered release entries with full title, date, and
    complete magnet link for each release. Note: one episode may
    have multiple releases (different languages/quality versions).

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
        return f"Error fetching releases: {e}"
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
    releases = target_group.get("releases", [])

    if not releases:
        return f"No releases found for group {group_name} (GroupID:{group_id})."

    lines = [
        f"Releases from {group_name} (GroupID:{group_id}) "
        f"for bangumi {bangumi_id}:",
        f"Total: {len(releases)} releases "
        f"(NOTE: multiple releases may be different language/quality "
        f"versions of the SAME episode — count unique episodes by "
        f"reading the titles carefully, do NOT assume "
        f"1 release = 1 episode)\n",
    ]
    for idx, release in enumerate(releases, 1):
        title = release.get("title", "")
        date = release.get("date", "")
        magnet = release.get("magnet", "")
        line = f"  #{idx}. {title}"
        if date:
            line += f"  ({date})"
        lines.append(line)
        if magnet:
            lines.append(f"      Magnet: {magnet}")

    return "\n".join(lines)
