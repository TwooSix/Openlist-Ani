from __future__ import annotations

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient

_STATUS_NAMES = {1: "wish", 2: "done", 3: "doing", 4: "on_hold", 5: "dropped"}


def _parse_params(
    collection_type: str,
    rate: str,
    comment: str,
    ep_status: str,
) -> tuple[int | None, int | None, str | None, int | None]:
    """Parse raw string params into typed values (None means unchanged)."""
    ct = int(collection_type) if collection_type else None
    r = int(rate) if rate else None
    cmt = comment or None
    ep = int(ep_status) if ep_status else None
    return ct, r, cmt, ep


def _build_summary(
    subject_id: str,
    ct: int | None,
    r: int | None,
    cmt: str | None,
    ep: int | None,
) -> str:
    """Build a human-readable summary of what was updated."""
    parts: list[str] = []
    if ct:
        parts.append(f"status={_STATUS_NAMES.get(ct, ct)}")
    if r is not None:
        parts.append(f"rate={r}/10")
    if ep is not None:
        parts.append(f"ep_status={ep}")
    if cmt:
        parts.append(f"comment='{cmt[:50]}...'")
    return f"Collection updated for subject {subject_id}: {', '.join(parts)}"


async def _apply_updates(
    client: BangumiClient,
    sid: int,
    ct: int | None,
    r: int | None,
    cmt: str | None,
    ep: int | None,
) -> None:
    """Send the actual API requests to Bangumi."""
    has_metadata = ct is not None or r is not None or cmt is not None

    # POST requires 'type'; default to 3 (doing) when not provided.
    if has_metadata:
        await client.post_user_collection(
            subject_id=sid,
            collection_type=ct if ct is not None else 3,
            rate=r,
            comment=cmt,
        )

    if ep is not None:
        # Ensure the subject is collected before updating episodes.
        if not has_metadata:
            await client.post_user_collection(
                subject_id=sid, collection_type=3,
            )
        await client.update_episode_progress(
            subject_id=sid, watched_eps=ep,
        )


async def run(
    subject_id: str = "",
    collection_type: str = "",
    rate: str = "",
    comment: str = "",
    ep_status: str = "",
    **kwargs,
) -> str:
    """Update a user's collection entry for an anime.

    Args:
        subject_id: Bangumi subject ID (required).
        collection_type: Status to set. 1=wish, 2=done, 3=doing,
            4=on_hold, 5=dropped. Empty = don't change.
        rate: Rating 0-10 (0 to remove rating). Empty = don't change.
        comment: Comment text. Empty = don't change.
        ep_status: Number of watched episodes. Empty = don't change.
    """
    if not subject_id:
        return "Error: 'subject_id' parameter is required."

    token = config.bangumi_token
    if not token:
        return "Error: Bangumi access token not configured."

    ct, r, cmt, ep = _parse_params(collection_type, rate, comment, ep_status)

    if ct is None and r is None and ep is None and cmt is None:
        return (
            "Error: At least one of collection_type, rate, "
            "comment, or ep_status must be provided."
        )

    client = BangumiClient(access_token=token)
    try:
        await _apply_updates(client, int(subject_id), ct, r, cmt, ep)
    except Exception as e:
        return f"Error updating collection: {e}"
    finally:
        await client.close()

    return _build_summary(subject_id, ct, r, cmt, ep)
