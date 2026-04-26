"""View the weekly anime airing calendar from Bangumi."""

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient

_WEEKDAY_NAMES = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}


def _resolve_weekday_name(weekday) -> str:
    """Return the best available display name for a weekday."""
    return (
        weekday.cn or weekday.en or _WEEKDAY_NAMES.get(weekday.id, f"Day {weekday.id}")
    )


def _display_name(name_cn: str | None, name: str | None) -> str:
    """Build a display name, preferring 'cn (en)' when both exist."""
    if name_cn and name:
        return f"{name_cn} ({name})"
    return name_cn or name or ""


def _format_item(item) -> str:
    """Format a single calendar anime item as a display line."""
    name = _display_name(item.name_cn, item.name)
    score_str = f" score:{item.rating.score}" if item.rating.score else ""
    votes_str = f" votes:{item.rating.total}" if item.rating.total else ""
    rank_str = f" rank:#{item.rank}" if item.rank else ""
    return f"  - [ID:{item.id}] {name}{score_str}{votes_str}{rank_str}"


def _format_day(day) -> list[str]:
    """Format a single calendar day as a list of display lines."""
    lines = [f"## {_resolve_weekday_name(day.weekday)}"]
    if not day.items:
        lines.append("  (no anime)")
    else:
        lines.extend(_format_item(item) for item in day.items)
    lines.append("")
    return lines


async def run(**kwargs) -> str:
    """Fetch the weekly anime airing calendar."""
    client = BangumiClient(access_token=config.bangumi_token)
    try:
        days = await client.fetch_calendar()
    except Exception as e:
        return f"Error fetching calendar: {e}"
    finally:
        await client.close()

    if not days:
        return "No calendar data available."

    lines = ["# Weekly Anime Calendar\n"]
    for day in days:
        lines.extend(_format_day(day))

    return "\n".join(lines)
