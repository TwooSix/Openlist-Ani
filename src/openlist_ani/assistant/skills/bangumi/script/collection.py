"""Bangumi user collection script — fetch user's anime collection."""

from __future__ import annotations

import logging

from .helper.client import _get_client

logger = logging.getLogger(__name__)


async def run(collection_type: int | None = None) -> str:
    """Fetch the current user's anime collection from Bangumi.

    Args:
        collection_type: Optional filter (1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped).

    Returns:
        Formatted collection text.
    """
    from openlist_ani.core.bangumi.model import SubjectType

    client = _get_client()
    try:
        entries = await client.fetch_user_collections(
            subject_type=SubjectType.ANIME,
            collection_type=collection_type,
        )
    except Exception as exc:
        logger.exception("Failed to fetch collections")
        return f"Failed to fetch Bangumi collection: {exc}"

    if not entries:
        return "No collection entries found."

    return _format_collections(entries)


def _format_collections(entries: list) -> str:
    """Format collection entries into readable text."""
    lines: list[str] = [f"Bangumi Collection ({len(entries)} entries)\n"]
    for entry in entries[:50]:
        name = ""
        if entry.subject:
            name = entry.subject.name_cn or entry.subject.name
        name = name or f"Subject#{entry.subject_id}"
        rate_str = f"rating:{entry.rate}" if entry.rate else "unrated"
        label = entry.collection_type_label
        lines.append(f"  - [{entry.subject_id}] {name} ({label}, {rate_str})")
        if entry.comment:
            lines.append(f"    Comment: {entry.comment}")

    if len(entries) > 50:
        lines.append(f"\n  ...and {len(entries) - 50} more entries")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Fetch Bangumi user collection")
    parser.add_argument(
        "--collection_type",
        type=int,
        default=None,
        help="Filter: 1=Wish 2=Done 3=Doing 4=OnHold 5=Dropped",
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(collection_type=args.collection_type)
            print(result)
        finally:
            from .helper.client import close_bangumi_client

            await close_bangumi_client()

    asyncio.run(_main())
