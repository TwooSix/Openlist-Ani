"""Get detailed info for a Bangumi subject."""

from typing import Any

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient


def _format_basic_info(subject) -> list[str]:
    """Format basic subject info (name, type, date, etc.)."""
    display = subject.display_name
    if subject.name_cn and subject.name:
        display = f"{subject.name_cn} ({subject.name})"

    lines = [
        f"# {display}",
        f"ID: {subject.id}",
        f"Type: {subject.type}",
    ]

    if subject.date:
        lines.append(f"Air date: {subject.date}")
    if subject.eps:
        lines.append(f"Episodes: {subject.eps}")
    if subject.platform:
        lines.append(f"Platform: {subject.platform}")

    if subject.rating:
        r = subject.rating
        lines.append(
            f"Rating: {r.score}/10 ({r.total} votes, rank #{r.rank})",
        )

    if subject.summary:
        summary = subject.summary
        if len(summary) > 500:
            summary = summary[:500] + "..."
        lines.append(f"\nSummary:\n{summary}")

    if subject.tags:
        tag_strs = [f"{t.name}({t.count})" for t in subject.tags[:15]]
        lines.append(f"\nTags: {', '.join(tag_strs)}")

    return lines


def _resolve_infobox_value(value: Any) -> str:
    """Resolve an infobox value (list of dicts or plain string) to text."""
    if isinstance(value, list):
        names = [v.get("v", "") for v in value if isinstance(v, dict)]
        return ", ".join(n for n in names if n)
    return str(value)


def _format_staff_and_cast(infobox: list[dict[str, Any]]) -> list[str]:
    """Format staff and cast lines from subject infobox."""
    staff_keys = {
        "导演", "原作", "脚本", "音乐", "制作",
        "动画制作", "总导演", "系列构成",
    }
    cast_key = "声优"
    staff_lines: list[str] = []
    cast_lines: list[str] = []

    for item in infobox:
        key = item.get("key", "")
        value = item.get("value", "")
        if not key or not value:
            continue
        display = _resolve_infobox_value(value)
        if not display:
            continue
        if key == cast_key:
            cast_lines.append(f"  {key}: {display[:200]}")
        elif key in staff_keys:
            staff_lines.append(f"  {key}: {display}")

    lines: list[str] = []
    if staff_lines:
        lines.append("\nStaff:")
        lines.extend(staff_lines)
    if cast_lines:
        lines.append("\nCast:")
        lines.extend(cast_lines)
    return lines


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

    lines = _format_basic_info(subject)

    if subject.infobox:
        lines.extend(_format_staff_and_cast(subject.infobox))

    return "\n".join(lines)
