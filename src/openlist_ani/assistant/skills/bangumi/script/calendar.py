"""Bangumi calendar script — fetch weekly anime airing calendar."""

from __future__ import annotations

import logging

from .helper.client import _get_client

logger = logging.getLogger(__name__)


async def run(weekday: int | None = None) -> str:
    """Fetch Bangumi weekly anime calendar.

    Args:
        weekday: Optional weekday filter (1=Monday .. 7=Sunday).

    Returns:
        Formatted calendar text.
    """
    client = _get_client()
    try:
        days = await client.fetch_calendar()
    except Exception as exc:
        logger.exception("Failed to fetch calendar")
        return f"Failed to fetch Bangumi calendar: {exc}"

    if weekday:
        days = [d for d in days if d.weekday.id == weekday]

    return _format_calendar(days)


def _format_calendar(days: list) -> str:
    """Format calendar days into readable text."""
    if not days:
        return "No calendar data found for the specified day."

    lines: list[str] = ["Bangumi Weekly Anime Calendar\n"]
    for day in days:
        lines.append(f"### {day.weekday.cn} ({day.weekday.en})")
        if not day.items:
            lines.append("  (no anime)")
            continue
        for item in day.items:
            score = f"score:{item.rating.score}" if item.rating.score else "unrated"
            lines.append(
                f"  - [{item.id}] {item.display_name} "
                f"({score}, rank #{item.rank or 'N/A'})"
            )
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Fetch Bangumi weekly anime calendar")
    parser.add_argument(
        "--weekday", type=int, default=None, help="Filter by weekday (1=Mon .. 7=Sun)"
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(weekday=args.weekday)
            print(result)
        finally:
            from .helper.client import close_bangumi_client

            await close_bangumi_client()

    asyncio.run(_main())
