"""List available fansub groups for a bangumi."""

from openlist_ani.config import config
from openlist_ani.core.mikan.client import MikanClient


async def run(
    bangumi_id: str = "",
    **kwargs,
) -> str:
    """List available fansub (subtitle) groups for a Mikan bangumi.

    Returns group names, IDs, and release counts. Use mikan/releases
    with a specific group_id to see that group's releases.

    Args:
        bangumi_id: Mikan bangumi ID (required).
    """
    if not bangumi_id:
        return "Error: 'bangumi_id' parameter is required."

    mikan = config.mikan
    client = MikanClient(
        username=mikan.username or "",
        password=mikan.password or "",
    )
    try:
        groups = await client.fetch_bangumi_subgroups(int(bangumi_id))
    except Exception as e:
        return f"Error fetching subgroups: {e}"
    finally:
        await client.close()

    if not groups:
        return f"No fansub groups found for bangumi {bangumi_id}."

    lines = [f"Fansub groups for bangumi {bangumi_id} ({len(groups)} groups):\n"]
    for group in groups:
        gid = group.get("id", "")
        name = group.get("name", "unknown")
        release_count = len(group.get("releases", []))
        lines.append(f"  - {name} (GroupID:{gid}, {release_count} releases)")

    lines.append("")
    lines.append(
        "NOTE: Release count ≠ episode count. One episode may have "
        "multiple releases (different languages, resolutions, or versions)."
    )
    lines.append(
        "Use mikan/releases with bangumi_id and group_id to see "
        "releases for a specific group."
    )

    return "\n".join(lines)
