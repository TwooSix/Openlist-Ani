"""Bangumi reviews script — fetch reviews and discussions for an anime."""

from __future__ import annotations

import logging

from .helper.client import _get_client

logger = logging.getLogger(__name__)


async def run(subject_id: int) -> str:
    """Fetch discussion topics and blog reviews for a subject.

    Args:
        subject_id: Bangumi subject ID.

    Returns:
        Formatted reviews and discussions text.
    """
    client = _get_client()
    try:
        topics, blogs = await client.fetch_subject_reviews(subject_id)
    except Exception as exc:
        logger.exception(f"Failed to fetch reviews for {subject_id}")
        return f"Failed to fetch reviews for subject {subject_id}: {exc}"

    return _format_reviews(subject_id, topics, blogs)


def _format_reviews(
    subject_id: int,
    topics: list,
    blogs: list,
) -> str:
    """Format topics and blogs into readable text."""
    lines: list[str] = [f"Reviews & Discussions for Subject #{subject_id}\n"]

    if topics:
        lines.append(f"### Discussion Topics ({len(topics)})")
        for t in topics[:15]:
            lines.append(f"  - {t.title} (by {t.user_nickname}, {t.replies} replies)")
    else:
        lines.append("### Discussion Topics: None found")

    lines.append("")

    if blogs:
        lines.append(f"### Blog Reviews ({len(blogs)})")
        for b in blogs[:15]:
            summary = b.summary[:200] if b.summary else "No summary"
            lines.append(f"  - [{b.title}] by {b.user_nickname} ({b.replies} replies)")
            lines.append(f"    {summary}")
    else:
        lines.append("### Blog Reviews: None found")

    if not topics and not blogs:
        lines.append("\nNo discussions or reviews found for this anime.")
    else:
        lines.append(
            "\n---\nPlease summarize the community opinions based "
            "on the topics and blog reviews above."
        )

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Fetch Bangumi reviews and discussions"
    )
    parser.add_argument(
        "--subject_id", type=int, required=True, help="Bangumi subject ID"
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(subject_id=args.subject_id)
            print(result)
        finally:
            from .helper.client import close_bangumi_client

            await close_bangumi_client()

    asyncio.run(_main())
