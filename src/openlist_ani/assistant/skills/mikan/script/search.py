"""Mikan search script — search anime on Mikan."""

from __future__ import annotations

import logging

from .helper.client import _get_mikan_client

logger = logging.getLogger(__name__)

_MIKAN_NOT_CONFIGURED_MSG = (
    "Mikan credentials not configured. Please set "
    "[mikan] username and password in config.toml."
)


async def run(keyword: str) -> str:
    """Search for anime on Mikan by keyword.

    Args:
        keyword: Search keyword (anime name).

    Returns:
        Formatted search results.
    """
    client = _get_mikan_client()
    if client is None:
        return _MIKAN_NOT_CONFIGURED_MSG

    try:
        results = await client.search_bangumi(keyword)
    except Exception as exc:
        logger.exception(f"Failed to search for '{keyword}'")
        return f"Failed to search Mikan: {exc}"

    if not results:
        return f"No results found on Mikan for '{keyword}'."

    lines = [f"Mikan Search Results for '{keyword}' ({len(results)} found)\n"]
    for item in results[:20]:
        lines.append(f"  - [ID:{item['bangumi_id']}] {item['name']} ({item['url']})")
    if len(results) > 20:
        lines.append(f"\n  ...and {len(results) - 20} more results")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Search anime on Mikan")
    parser.add_argument(
        "--keyword", type=str, required=True, help="Search keyword (anime name)"
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(keyword=args.keyword)
            print(result)
        finally:
            from .helper.client import close_mikan_client

            await close_mikan_client()

    asyncio.run(_main())
