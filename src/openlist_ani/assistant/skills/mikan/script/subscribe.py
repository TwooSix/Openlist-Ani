"""Mikan subscribe script — subscribe to anime RSS on Mikan."""

from __future__ import annotations

import logging
from typing import Any

from .helper.client import _get_mikan_client

logger = logging.getLogger(__name__)

_MIKAN_NOT_CONFIGURED_MSG = (
    "Mikan credentials not configured. Please set "
    "[mikan] username and password in config.toml."
)


async def run(
    bangumi_id: int,
    subtitle_group_name: str | None = None,
    language: int | None = None,
) -> str:
    """Subscribe to an anime on Mikan with optional subtitle group.

    Args:
        bangumi_id: Mikan bangumi ID.
        subtitle_group_name: Optional fansub group name to resolve.
        language: Optional language filter.

    Returns:
        Success or error message.
    """
    client = _get_mikan_client()
    if client is None:
        return _MIKAN_NOT_CONFIGURED_MSG

    # Resolve subtitle group name → ID
    subtitle_group_id: int | None = None
    if subtitle_group_name:
        result = await _resolve_subtitle_group(client, bangumi_id, subtitle_group_name)
        if isinstance(result, str):
            return result
        subtitle_group_id = result

    try:
        success = await client.subscribe_bangumi(
            bangumi_id=bangumi_id,
            subtitle_group_id=subtitle_group_id,
            language=language,
        )
    except Exception as exc:
        logger.exception(f"Failed to subscribe bangumi {bangumi_id}")
        return f"Failed to subscribe on Mikan: {exc}"

    if success:
        return _format_success(
            bangumi_id, subtitle_group_name, subtitle_group_id, language
        )

    return (
        f"Failed to subscribe to Mikan bangumi {bangumi_id}. "
        "Check credentials or bangumi ID."
    )


async def _resolve_subtitle_group(
    client: Any,
    bangumi_id: int,
    subtitle_group_name: str,
) -> int | str:
    """Resolve subtitle group name to ID."""
    try:
        subgroups = await client.fetch_bangumi_subgroups(bangumi_id)
    except Exception as exc:
        logger.warning(f"Failed to fetch subgroups for bangumi {bangumi_id}: {exc}")
        subgroups = []

    if not subgroups:
        return (
            f"No subtitle groups found for bangumi {bangumi_id}. "
            f"Cannot resolve '{subtitle_group_name}'."
        )

    # Exact match first, then substring match
    matched = next(
        (g for g in subgroups if g["name"] == subtitle_group_name),
        None,
    )
    if matched is None:
        matched = next(
            (
                g
                for g in subgroups
                if subtitle_group_name in g["name"] or g["name"] in subtitle_group_name
            ),
            None,
        )
    if matched:
        logger.info(
            f"Resolved '{subtitle_group_name}'"
            f" → subgroup ID {matched['id']} ({matched['name']})"
        )
        return matched["id"]

    available = ", ".join(f"{g['name']}(ID:{g['id']})" for g in subgroups)
    return (
        f"Subtitle group '{subtitle_group_name}' not found "
        f"for bangumi {bangumi_id}. "
        f"Available groups: {available}"
    )


def _format_success(
    bangumi_id: int,
    subtitle_group_name: str | None,
    subtitle_group_id: int | None,
    language: int | None,
) -> str:
    """Format a success message for subscription."""
    parts = [f"Successfully subscribed to Mikan bangumi {bangumi_id}"]
    if subtitle_group_id is not None:
        parts.append(f"subtitle group: {subtitle_group_name} (ID: {subtitle_group_id})")
    lang_labels = {
        0: "all",
        1: "Simplified Chinese",
        2: "Traditional Chinese",
    }
    if language is not None:
        parts.append(f"language: {lang_labels.get(language, str(language))}")
    return " | ".join(parts)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Subscribe to a bangumi on Mikan")
    parser.add_argument(
        "--bangumi_id", type=int, required=True, help="Mikan bangumi ID"
    )
    parser.add_argument(
        "--subtitle_group_name",
        type=str,
        default=None,
        help="Subtitle group name to subscribe to",
    )
    parser.add_argument(
        "--language",
        type=int,
        default=None,
        help="0=all 1=Simplified Chinese 2=Traditional Chinese",
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        try:
            result = await run(
                bangumi_id=args.bangumi_id,
                subtitle_group_name=args.subtitle_group_name,
                language=args.language,
            )
            print(result)
        finally:
            from .helper.client import close_mikan_client

            await close_mikan_client()

    asyncio.run(_main())
