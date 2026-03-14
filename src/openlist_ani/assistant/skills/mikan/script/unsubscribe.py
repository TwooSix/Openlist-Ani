"""Mikan unsubscribe script — unsubscribe from anime on Mikan."""

from __future__ import annotations

import logging

from .helper.client import _get_mikan_client

logger = logging.getLogger(__name__)

_MIKAN_NOT_CONFIGURED_MSG = (
    "Mikan credentials not configured. Please set "
    "[mikan] username and password in config.toml."
)


async def run(
    bangumi_id: int,
    subtitle_group_id: int | None = None,
) -> str:
    """Unsubscribe from an anime on Mikan.

    Args:
        bangumi_id: Mikan bangumi ID.
        subtitle_group_id: Optional fansub group ID.

    Returns:
        Success or error message.
    """
    client = _get_mikan_client()
    if client is None:
        return _MIKAN_NOT_CONFIGURED_MSG

    try:
        success = await client.unsubscribe_bangumi(
            bangumi_id=bangumi_id,
            subtitle_group_id=subtitle_group_id,
        )
    except Exception as exc:
        logger.exception(f"Failed to unsubscribe bangumi {bangumi_id}")
        return f"Failed to unsubscribe on Mikan: {exc}"

    if success:
        return f"Successfully unsubscribed from Mikan bangumi {bangumi_id}"

    return (
        f"Failed to unsubscribe from Mikan bangumi {bangumi_id}. "
        "Check credentials or bangumi ID."
    )


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Unsubscribe from a bangumi on Mikan")
    parser.add_argument(
        "--bangumi_id", type=int, required=True, help="Mikan bangumi ID"
    )
    parser.add_argument(
        "--subtitle_group_id", type=int, default=None, help="Optional subtitle group ID"
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(
                bangumi_id=args.bangumi_id,
                subtitle_group_id=args.subtitle_group_id,
            )
            print(result)
        finally:
            from .helper.client import close_mikan_client

            await close_mikan_client()

    asyncio.run(_main())
