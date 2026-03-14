"""Bangumi subject detail script — fetch anime details by ID(s)."""

from __future__ import annotations

import asyncio
import logging

from openlist_ani.core.bangumi.model import BangumiSubject

from .helper.client import _get_client

logger = logging.getLogger(__name__)


def _format_subject(subject: BangumiSubject) -> str:
    """Format a single subject into human-readable text."""
    tags_str = ", ".join(f"{t.name}({t.count})" for t in subject.tags[:15])
    summary_text = subject.summary[:500] if subject.summary else "N/A"

    return (
        f"Subject: {subject.display_name}\n"
        f"  ID: {subject.id} | Type: {subject.type}"
        f" | Platform: {subject.platform}\n"
        f"  Date: {subject.date}"
        f" | Episodes: {subject.total_episodes}\n"
        f"  Rating: {subject.rating.score} "
        f"(rank #{subject.rating.rank}, "
        f"{subject.rating.total} votes)\n"
        f"  Tags: {tags_str}\n"
        f"  URL: {subject.url}\n\n"
        f"  Summary:\n{summary_text}"
    )


async def _fetch_one(subject_id: int) -> str:
    """Fetch and format a single subject, returning error text on failure."""
    client = _get_client()
    try:
        subject = await client.fetch_subject(subject_id)
    except Exception as exc:
        logger.exception(f"Failed to fetch subject {subject_id}")
        return f"[Subject {subject_id}] Failed: {exc}"
    return _format_subject(subject)


async def run(subject_ids: list[int]) -> str:
    """Fetch detailed information about one or more Bangumi subjects.

    Multiple subjects are fetched concurrently.

    Args:
        subject_ids: List of Bangumi subject IDs.

    Returns:
        Formatted subject detail text (sections separated by dividers).
    """
    if not subject_ids:
        return "Error: no subject_ids provided."

    results = await asyncio.gather(*[_fetch_one(sid) for sid in subject_ids])
    return "\n\n---\n\n".join(results)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch Bangumi subject details")
    parser.add_argument(
        "--subject_id",
        type=int,
        nargs="+",
        required=True,
        help="One or more Bangumi subject IDs",
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(subject_ids=args.subject_id)
            print(result)
        finally:
            from .helper.client import close_bangumi_client

            await close_bangumi_client()

    asyncio.run(_main())
