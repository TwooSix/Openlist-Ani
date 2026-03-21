"""Mikan get details script — get available subtitle groups for a bangumi."""

from __future__ import annotations

import logging

from .helper.client import _get_mikan_client

logger = logging.getLogger(__name__)

_MIKAN_NOT_CONFIGURED_MSG = (
    "Mikan credentials not configured. Please set "
    "[mikan] username and password in config.toml."
)


async def run(bangumi_id: int) -> str:
    """Get details (subtitle groups + their latest episodes) for an anime.

    Args:
        bangumi_id: Mikan bangumi ID.

    Returns:
        Formatted details string including each group's latest resources.
    """
    client = _get_mikan_client()
    if client is None:
        return _MIKAN_NOT_CONFIGURED_MSG

    try:
        subgroups = await client.fetch_bangumi_subgroups(bangumi_id)
    except Exception as exc:
        logger.exception(f"Failed to get details for bangumi {bangumi_id}")
        return f"Failed to fetch details from Mikan: {exc}"

    if not subgroups:
        return f"No subtitle groups found for Mikan bangumi {bangumi_id}."

    lines = [f"Bangumi ID {bangumi_id} — {len(subgroups)} subtitle group(s) found:\n"]
    for group in subgroups:
        lines.append(f"  【{group['name']}】 (ID: {group['id']})")
        episodes = group.get("episodes", [])
        if episodes:
            for ep in episodes:
                date_str = f" ({ep['date']})" if ep.get("date") else ""
                lines.append(f"    - {ep['title']}{date_str}")
        else:
            lines.append("    (no recent episodes found)")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Get bangumi details on Mikan")
    parser.add_argument(
        "--bangumi_id", type=int, required=True, help="Mikan bangumi ID"
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(bangumi_id=args.bangumi_id)
            print(result)
        finally:
            from .helper.client import close_mikan_client

            await close_mikan_client()

    asyncio.run(_main())
