"""Update collection status, rating, or episode progress on Bangumi."""

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient


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

    ct = int(collection_type) if collection_type else None
    r = int(rate) if rate else None
    ep = int(ep_status) if ep_status else None
    cmt = comment if comment else None

    if ct is None and r is None and ep is None and cmt is None:
        return "Error: At least one of collection_type, rate, comment, or ep_status must be provided."

    client = BangumiClient(access_token=token)
    try:
        await client.post_user_collection(
            subject_id=int(subject_id),
            collection_type=ct,
            rate=r,
            comment=cmt,
            ep_status=ep,
        )
    except Exception as e:
        return f"Error updating collection: {e}"
    finally:
        await client.close()

    updates = []
    if ct:
        names = {1: "wish", 2: "done", 3: "doing", 4: "on_hold", 5: "dropped"}
        updates.append(f"status={names.get(ct, ct)}")
    if r is not None:
        updates.append(f"rate={r}/10")
    if ep is not None:
        updates.append(f"ep_status={ep}")
    if cmt:
        updates.append(f"comment='{cmt[:50]}...'")

    return f"Collection updated for subject {subject_id}: {', '.join(updates)}"
