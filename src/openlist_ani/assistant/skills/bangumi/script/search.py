"""Bangumi subject search script — search anime by keyword and filters."""

from __future__ import annotations

import logging
from typing import Any

from .helper.client import _get_client

logger = logging.getLogger(__name__)

_SUBJECT_TYPE_LABELS: dict[int, str] = {
    1: "Book",
    2: "Anime",
    3: "Music",
    4: "Game",
    6: "Real",
}


def _format_result(item: dict[str, Any], idx: int) -> str:
    """Format a single search result item into human-readable text."""
    sid = item.get("id", "?")
    name = item.get("name", "")
    name_cn = item.get("name_cn", "")
    display = name_cn if name_cn else name
    type_val = item.get("type", 0)
    type_label = _SUBJECT_TYPE_LABELS.get(type_val, str(type_val))
    date = item.get("date", "N/A")

    rating = item.get("rating", {})
    score = rating.get("score", 0)
    rank = rating.get("rank", 0)

    tags = item.get("tags", [])
    tags_str = ", ".join(t.get("name", "") for t in tags[:8]) if tags else ""

    summary = item.get("summary", "") or item.get("short_summary", "")
    if summary:
        summary = summary[:200]

    lines = [
        f"{idx}. {display}",
        f"   ID: {sid} | Type: {type_label} | Date: {date}",
        f"   Score: {score} | Rank: #{rank}",
    ]
    if tags_str:
        lines.append(f"   Tags: {tags_str}")
    if name_cn and name:
        lines.append(f"   Original: {name}")
    if summary:
        lines.append(f"   Summary: {summary}")
    lines.append(f"   URL: https://bgm.tv/subject/{sid}")
    return "\n".join(lines)


async def run(
    keyword: str,
    *,
    sort: str = "match",
    subject_type: list[int] | None = None,
    tag: list[str] | None = None,
    air_date: list[str] | None = None,
    rating: list[str] | None = None,
    rank: list[str] | None = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Search Bangumi subjects by keyword.

    Args:
        keyword: Search keyword.
        sort: Sort order – match, heat, rank, score.
        subject_type: Filter by type (1=Book,2=Anime,3=Music,4=Game,6=Real).
        tag: Filter by tags (AND relation).
        air_date: Date range filters, e.g. [">=2020-07-01"].
        rating: Rating range filters, e.g. [">=6"].
        rank: Rank range filters, e.g. [">10"].
        limit: Max results per page.
        offset: Pagination offset.

    Returns:
        Formatted search results text.
    """
    client = _get_client()
    try:
        data = await client.search_subjects(
            keyword,
            sort=sort,
            subject_type=subject_type,
            tag=tag,
            air_date=air_date,
            rating=rating,
            rank=rank,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.exception(f"Failed to search subjects for '{keyword}'")
        return f"Search failed: {exc}"

    total = data.get("total", 0)
    items = data.get("data", [])

    if not items:
        return f"No results found for '{keyword}'."

    header = f"Search results for '{keyword}' ({total} total, showing {len(items)}):\n"
    formatted = [_format_result(item, offset + i + 1) for i, item in enumerate(items)]
    return header + "\n\n".join(formatted)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Search Bangumi subjects")
    parser.add_argument("--keyword", type=str, required=True, help="Search keyword")
    parser.add_argument(
        "--sort",
        type=str,
        default="match",
        choices=["match", "heat", "rank", "score"],
        help="Sort order (default: match)",
    )
    parser.add_argument(
        "--type",
        type=int,
        nargs="*",
        dest="subject_type",
        help="Subject type filter (1=Book,2=Anime,3=Music,4=Game,6=Real)",
    )
    parser.add_argument("--tag", type=str, nargs="*", help="Tag filter (AND relation)")
    parser.add_argument(
        "--air_date", type=str, nargs="*", help="Air date filters, e.g. >=2020-07-01"
    )
    parser.add_argument(
        "--rating", type=str, nargs="*", help="Rating filters, e.g. >=6"
    )
    parser.add_argument("--rank", type=str, nargs="*", help="Rank filters, e.g. >10")
    parser.add_argument("--limit", type=int, default=25, help="Max results per page")
    parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(
                keyword=args.keyword,
                sort=args.sort,
                subject_type=args.subject_type,
                tag=args.tag,
                air_date=args.air_date,
                rating=args.rating,
                rank=args.rank,
                limit=args.limit,
                offset=args.offset,
            )
            print(result)
        finally:
            from .helper.client import close_bangumi_client

            await close_bangumi_client()

    asyncio.run(_main())
