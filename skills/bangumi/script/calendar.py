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
        # day.weekday is a Weekday dataclass with .id, .cn, .en, .ja
        weekday = (
            day.weekday.cn
            or day.weekday.en
            or _WEEKDAY_NAMES.get(day.weekday.id, f"Day {day.weekday.id}")
        )
        lines.append(f"## {weekday}")

        if not day.items:
            lines.append("  (no anime)")
        else:
            for item in day.items:
                name = item.name_cn or item.name
                if item.name_cn and item.name:
                    name = f"{item.name_cn} ({item.name})"
                score_str = f" score:{item.rating.score}" if item.rating.score else ""
                rank_str = f" rank:#{item.rank}" if item.rank else ""
                lines.append(f"  - [ID:{item.id}] {name}{score_str}{rank_str}")

        lines.append("")

    return "\n".join(lines)
