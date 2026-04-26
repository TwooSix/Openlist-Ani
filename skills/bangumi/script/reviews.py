"""Fetch community reviews and discussions for a Bangumi subject."""

from datetime import datetime, timezone

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient


def _format_timestamp(ts: int) -> str:
    """Format a Unix timestamp to a readable date string.

    Args:
        ts: Unix timestamp.

    Returns:
        Formatted date string, or empty if timestamp is 0.
    """
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _format_date_suffix(ts: int) -> str:
    """Build a ' (YYYY-MM-DD)' suffix, or empty string if no timestamp."""
    date = _format_timestamp(ts)
    return f" ({date})" if date else ""


def _format_topics(topics: list, lines: list[str]) -> None:
    """Append formatted discussion topics to *lines*."""
    lines.append(f"## Discussion Topics ({len(topics)})")
    for t in topics[:10]:
        date_str = _format_date_suffix(t.timestamp)
        lines.append(
            f"  - {t.title}  by {t.user_nickname}{date_str}  replies:{t.replies}",
        )
    lines.append("")


def _format_blogs(blogs: list, lines: list[str]) -> None:
    """Append formatted blog reviews to *lines*."""
    lines.append(f"## Blog Reviews ({len(blogs)})")
    for b in blogs[:10]:
        date_str = _format_date_suffix(b.timestamp)
        summary = b.summary[:150] + "..." if len(b.summary) > 150 else b.summary
        lines.append(
            f"  - {b.title}  by {b.user_nickname}{date_str}  replies:{b.replies}",
        )
        if summary:
            lines.append(f"    {summary}")
    lines.append("")


async def run(
    subject_id: str = "",
    **kwargs,
) -> str:
    """Fetch community discussion topics and blog reviews for a subject.

    Args:
        subject_id: Bangumi subject ID (required).
    """
    if not subject_id:
        return "Error: 'subject_id' parameter is required."

    client = BangumiClient(access_token=config.bangumi_token)
    try:
        topics, blogs = await client.fetch_subject_reviews(int(subject_id))
    except Exception as e:
        return f"Error fetching reviews for subject {subject_id}: {e}"
    finally:
        await client.close()

    lines: list[str] = [f"Community reviews for subject {subject_id}:\n"]

    if topics:
        _format_topics(topics, lines)
    if blogs:
        _format_blogs(blogs, lines)
    if not topics and not blogs:
        lines.append("No community reviews found.")

    return "\n".join(lines)
