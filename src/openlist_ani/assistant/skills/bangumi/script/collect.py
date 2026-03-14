"""Bangumi collection update script — update collection status and watch progress."""

from __future__ import annotations

import logging
from typing import Any

from .helper.client import _get_client

logger = logging.getLogger(__name__)


async def run(
    subject_id: int,
    collection_type: int,
    ep_status: int | None = None,
    episode_number: int | None = None,
    episode_numbers: list[int] | None = None,
    episode_collection_type: int = 2,
) -> str:
    """Update an anime's collection status or watch progress.

    Args:
        subject_id: Bangumi subject ID.
        collection_type: Collection type (1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped).
        ep_status: Optional "watched to episode N" progress.
        episode_number: Optional single episode number.
        episode_numbers: Optional list of episode numbers.
        episode_collection_type: Episode status type (0=Remove, 1=Wish, 2=Done, 3=Dropped).

    Returns:
        Success or error message.
    """

    error = _validate_params(
        collection_type,
        ep_status,
        episode_number,
        episode_numbers,
        episode_collection_type,
    )
    if error:
        return error

    requested_episode_numbers = _resolve_episode_numbers(
        ep_status,
        episode_number,
        episode_numbers,
    )

    client = _get_client()
    try:
        (
            matched_episode_ids,
            missing_episode_numbers_list,
            rollback_episode_ids,
        ) = await _pre_validate_episodes(
            client,
            subject_id,
            requested_episode_numbers,
            ep_status,
            episode_number,
            episode_numbers,
        )
        if isinstance(matched_episode_ids, str):
            return matched_episode_ids

        await _apply_collection_updates(
            client,
            subject_id,
            collection_type,
            ep_status,
            requested_episode_numbers,
            matched_episode_ids,
            rollback_episode_ids,
            episode_collection_type,
        )
    except Exception as exc:
        logger.exception(f"Failed to update collection for subject {subject_id}")
        return f"Failed to update collection for subject {subject_id}: {exc}"

    return _format_result(
        subject_id,
        collection_type,
        requested_episode_numbers,
        matched_episode_ids,
        missing_episode_numbers_list,
        rollback_episode_ids,
        ep_status,
    )


def _validate_params(
    collection_type: int,
    ep_status: int | None,
    episode_number: int | None,
    episode_numbers: list[int] | None,
    episode_collection_type: int,
) -> str | None:
    """Validate input parameters."""
    from openlist_ani.core.bangumi.model import CollectionType

    valid_types = {t.value for t in CollectionType}
    if collection_type not in valid_types:
        return (
            f"Invalid collection type: {collection_type}. "
            f"Valid: 1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped"
        )
    if ep_status is not None and ep_status < 0:
        return f"Invalid ep_status: {ep_status}. Must be >= 0."
    if episode_number is not None and episode_number <= 0:
        return f"Invalid episode_number: {episode_number}. Must be >= 1."
    if episode_numbers is not None and any(n <= 0 for n in episode_numbers):
        return "Invalid episode_numbers: all values must be >= 1."
    if episode_collection_type not in {0, 1, 2, 3}:
        return (
            f"Invalid episode_collection_type: {episode_collection_type}. "
            "Valid: 0=Remove, 1=Wish, 2=Done, 3=Dropped"
        )
    return None


def _resolve_episode_numbers(
    ep_status: int | None,
    episode_number: int | None,
    episode_numbers: list[int] | None,
) -> list[int]:
    """Determine the list of requested episode numbers."""
    if episode_numbers:
        return sorted(set(episode_numbers))
    if episode_number is not None:
        return [episode_number]
    if ep_status is not None and ep_status > 0:
        return list(range(1, ep_status + 1))
    return []


def _build_number_to_id_map(episodes: list[dict]) -> dict[int, int]:
    """Build mapping from episode number to episode ID."""
    number_to_id: dict[int, int] = {}
    for ep in episodes:
        number = ep.get("ep") or ep.get("sort")
        episode_id = ep.get("id")
        if not number or not episode_id:
            continue
        try:
            episode_number_value = int(number)
        except (TypeError, ValueError):
            continue
        if episode_number_value > 0:
            number_to_id[episode_number_value] = int(episode_id)
    return number_to_id


async def _pre_validate_episodes(
    client: Any,
    subject_id: int,
    requested_episode_numbers: list[int],
    ep_status: int | None,
    episode_number: int | None,
    episode_numbers: list[int] | None,
) -> tuple[list[int] | str, list[int], list[int]]:
    """Pre-validate requested episodes and compute rollback."""
    matched_episode_ids: list[int] = []
    missing_episode_numbers_list: list[int] = []
    rollback_episode_ids: list[int] = []

    if not requested_episode_numbers:
        return matched_episode_ids, missing_episode_numbers_list, rollback_episode_ids

    episodes = await client.fetch_subject_episodes(
        subject_id=subject_id,
        episode_type=0,
    )
    number_to_id = _build_number_to_id_map(episodes)
    available_numbers = sorted(number_to_id)
    max_available = available_numbers[-1] if available_numbers else 0

    for num in requested_episode_numbers:
        eid = number_to_id.get(num)
        if eid is not None:
            matched_episode_ids.append(eid)
        else:
            missing_episode_numbers_list.append(num)

    if missing_episode_numbers_list:
        return (
            (
                f"⚠️ MISMATCH — subject {subject_id} only has "
                f"episodes 1-{max_available} on Bangumi, but "
                f"requested up to episode "
                f"{requested_episode_numbers[-1]}. "
                f"Unmatched: {missing_episode_numbers_list}. "
                "ACTION REQUIRED: Tell the user about this "
                "discrepancy and ask them to clarify. "
                "DO NOT call this tool again with modified "
                "episode numbers."
            ),
            missing_episode_numbers_list,
            rollback_episode_ids,
        )

    if not matched_episode_ids:
        return (
            (
                f"⚠️ MISMATCH — subject {subject_id}: no valid "
                "episodes matched. ACTION REQUIRED: Relay this to "
                "the user and ask for correct episode numbers. "
                "DO NOT retry with different parameters."
            ),
            missing_episode_numbers_list,
            rollback_episode_ids,
        )

    # Support rollback when user explicitly sets progress via ep_status.
    if ep_status is not None and episode_number is None and not episode_numbers:
        rollback_episode_ids = await _compute_rollback(
            client,
            subject_id,
            ep_status,
            number_to_id,
        )

    return matched_episode_ids, missing_episode_numbers_list, rollback_episode_ids


async def _compute_rollback(
    client: Any,
    subject_id: int,
    ep_status: int,
    number_to_id: dict[int, int],
) -> list[int]:
    """Compute episode IDs to rollback when lowering watch progress."""
    from openlist_ani.core.bangumi.model import SubjectType

    entries = await client.fetch_user_collections(
        subject_type=SubjectType.ANIME,
    )
    current_entry = next(
        (entry for entry in entries if entry.subject_id == subject_id),
        None,
    )
    current_ep_status = current_entry.ep_status if current_entry else 0
    if current_ep_status <= ep_status:
        return []
    rollback_numbers = list(range(ep_status + 1, current_ep_status + 1))
    return [number_to_id[num] for num in rollback_numbers if num in number_to_id]


async def _apply_collection_updates(
    client: Any,
    subject_id: int,
    collection_type: int,
    ep_status: int | None,
    requested_episode_numbers: list[int],
    matched_episode_ids: list[int],
    rollback_episode_ids: list[int],
    episode_collection_type: int,
) -> None:
    """Apply collection and episode updates to Bangumi API."""
    await client.post_user_collection(
        subject_id=subject_id,
        collection_type=collection_type,
        ep_status=None if requested_episode_numbers else ep_status,
    )
    if matched_episode_ids:
        await client.patch_subject_episode_collections(
            subject_id=subject_id,
            episode_ids=matched_episode_ids,
            collection_type=episode_collection_type,
        )
    if rollback_episode_ids:
        await client.patch_subject_episode_collections(
            subject_id=subject_id,
            episode_ids=rollback_episode_ids,
            collection_type=0,
        )


def _format_result(
    subject_id: int,
    collection_type: int,
    requested_episode_numbers: list[int],
    matched_episode_ids: list[int],
    missing_episode_numbers: list[int],
    rollback_episode_ids: list[int],
    ep_status: int | None,
) -> str:
    """Format the success result message."""
    from openlist_ani.core.bangumi.model import COLLECTION_TYPE_LABELS

    type_label = COLLECTION_TYPE_LABELS.get(collection_type, "unknown")
    parts = [f"Successfully updated subject {subject_id} -> {type_label}"]
    if requested_episode_numbers:
        parts.append(f"Episode updates: {len(matched_episode_ids)} matched")
        parts.append(f"Requested episodes: {requested_episode_numbers}")
        if missing_episode_numbers:
            parts.append(f"Not found: {missing_episode_numbers}")
        if rollback_episode_ids:
            parts.append(f"Rollback cleared episodes: {len(rollback_episode_ids)}")
    elif ep_status is not None:
        parts.append(f"Episodes watched: {ep_status}")
    return " | ".join(parts)


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Update Bangumi collection status")
    parser.add_argument(
        "--subject_id", type=int, required=True, help="Bangumi subject ID"
    )
    parser.add_argument(
        "--collection_type",
        type=int,
        required=True,
        help="1=Wish 2=Done 3=Doing 4=OnHold 5=Dropped",
    )
    parser.add_argument(
        "--ep_status", type=int, default=None, help="Watched-to episode number"
    )
    parser.add_argument(
        "--episode_number", type=int, default=None, help="Single episode number"
    )
    parser.add_argument(
        "--episode_numbers",
        type=str,
        default=None,
        help="Comma-separated episode numbers, e.g. 1,2,3",
    )
    parser.add_argument(
        "--episode_collection_type",
        type=int,
        default=2,
        help="0=Remove 1=Wish 2=Done 3=Dropped",
    )
    args = parser.parse_args()

    async def _main() -> None:
        from openlist_ani.config import config  # noqa: F401

        episode_numbers = None
        if args.episode_numbers:
            episode_numbers = [int(x.strip()) for x in args.episode_numbers.split(",")]

        try:
            result = await run(
                subject_id=args.subject_id,
                collection_type=args.collection_type,
                ep_status=args.ep_status,
                episode_number=args.episode_number,
                episode_numbers=episode_numbers,
                episode_collection_type=args.episode_collection_type,
            )
            print(result)
        finally:
            from .helper.client import close_bangumi_client

            await close_bangumi_client()

    asyncio.run(_main())
