"""List the user's anime collection on Bangumi."""

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient

_COLLECTION_TYPES = {
    1: "wish (想看)",
    2: "done (看过)",
    3: "doing (在看)",
    4: "on_hold (搁置)",
    5: "dropped (抛弃)",
}


def _display_name(name_cn: str | None, name: str | None) -> str:
    """Build a display name, preferring 'cn (en)' when both exist."""
    if name_cn and name:
        return f"{name_cn} ({name})"
    return name_cn or name or ""


def _format_entry(entry) -> str:
    """Format a single collection entry as a display line."""
    subject = entry.subject
    name = _display_name(subject.name_cn, subject.name)
    detail = f"[ID:{subject.id}] {name}"
    if entry.rate:
        detail += f"  rated:{entry.rate}/10"
    if entry.ep_status:
        detail += f"  ep:{entry.ep_status}"
    lines = [f"  - {detail}"]
    if entry.comment:
        lines.append(f"    comment: {entry.comment}")
    if entry.tags:
        lines.append(f"    tags: {', '.join(entry.tags)}")
    return "\n".join(lines)


def _group_entries(entries) -> dict[int, list]:
    """Group collection entries by their collection type."""
    grouped: dict[int, list] = {}
    for entry in entries:
        grouped.setdefault(entry.type, []).append(entry)
    return grouped


def _format_groups(entries) -> str:
    """Format grouped collection entries into display text."""
    grouped = _group_entries(entries)
    lines = [f"Total: {len(entries)} entries\n"]
    for ct_val in sorted(grouped):
        group = grouped[ct_val]
        ct_name = _COLLECTION_TYPES.get(ct_val, f"type_{ct_val}")
        lines.append(f"## {ct_name} ({len(group)})")
        lines.extend(_format_entry(entry) for entry in group)
        lines.append("")
    return "\n".join(lines)


async def run(
    collection_type: str = "",
    subject_type: str = "2",
    **kwargs,
) -> str:
    """List the user's collection entries.

    Args:
        collection_type: Filter by status. 1=wish, 2=done, 3=doing,
            4=on_hold, 5=dropped. Empty = all.
        subject_type: Subject type. 2=anime (default), 1=book, 4=game.
    """
    token = config.bangumi_token
    if not token:
        return "Error: Bangumi access token not configured. Set [bangumi] access_token in config.toml."

    client = BangumiClient(access_token=token)
    try:
        ct = int(collection_type) if collection_type else None
        st = int(subject_type) if subject_type else 2
        entries = await client.fetch_user_collections(
            subject_type=st,
            collection_type=ct,
        )
    except Exception as e:
        return f"Error fetching collections: {e}"
    finally:
        await client.close()

    if not entries:
        filter_name = _COLLECTION_TYPES.get(ct, "all") if ct else "all"
        return f"No collection entries found (filter: {filter_name})."

    return _format_groups(entries)
