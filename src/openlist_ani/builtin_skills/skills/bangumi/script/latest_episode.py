"""Get the latest aired main-story episode for a Bangumi subject."""

from __future__ import annotations

from datetime import date
from typing import Any

from openlist_ani.assistant.skill_support.bangumi_client import BangumiClient
from openlist_ani.adapters.outbound.configuration import config


def _parse_date(value: str) -> date | None:
    """Parse a Bangumi date string, returning None for blank or invalid dates."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_as_of_date(value: str) -> date:
    """Parse the optional reference date for deterministic latest-episode lookup."""
    if not value:
        return date.today()
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError("as_of_date must use YYYY-MM-DD format")
    return parsed


def _numeric(value: Any) -> float:
    """Convert episode sort values to a stable numeric fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _episode_date(episode: dict[str, Any]) -> date | None:
    """Return the parsed episode airdate."""
    return _parse_date(str(episode.get("airdate") or ""))


def _find_latest_aired_episode(
    episodes: list[dict[str, Any]],
    as_of: date,
) -> dict[str, Any] | None:
    """Return the latest episode whose Bangumi airdate is on or before as_of."""
    aired = [
        episode
        for episode in episodes
        if (airdate := _episode_date(episode)) is not None and airdate <= as_of
    ]
    if not aired:
        return None
    return max(
        aired,
        key=lambda episode: (
            _episode_date(episode) or date.min,
            _numeric(episode.get("sort", episode.get("ep"))),
            _numeric(episode.get("ep", episode.get("sort"))),
            int(episode.get("id") or 0),
        ),
    )


def _format_sort(value: Any) -> str:
    """Format Bangumi numeric episode values without trailing .0."""
    if value is None or value == "":
        return ""
    number = _numeric(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _episode_number(episode: dict[str, Any]) -> str:
    """Return the best human-facing episode number."""
    return _format_sort(episode.get("ep")) or _format_sort(episode.get("sort")) or "?"


def _episode_title(episode: dict[str, Any]) -> str:
    """Return the best available title for an episode."""
    return str(episode.get("name_cn") or episode.get("name") or "(untitled)")


def _subject_name(subject: Any, subject_id: str) -> str:
    """Return a stable display name for a subject-like object."""
    return str(getattr(subject, "display_name", "") or f"ID:{subject_id}")


def _format_no_episode(subject_name: str, as_of: date, episode_count: int) -> str:
    """Format the no-aired-episode result."""
    return "\n".join(
        [
            f"No aired main-story episodes found for {subject_name} as of {as_of.isoformat()}.",
            f"Known main-story episodes: {episode_count}",
            "Note: Bangumi episode airdate has date precision only; exact broadcast time is not available.",
        ],
    )


def _format_latest_episode(
    subject_name: str,
    episode: dict[str, Any],
    as_of: date,
    episode_count: int,
) -> str:
    """Format the latest aired episode result for the assistant."""
    airdate = episode.get("airdate") or ""
    lines = [
        f"# Latest aired episode for {subject_name}",
        f"As of: {as_of.isoformat()}",
        f"Episode: ep.{_episode_number(episode)}",
        f"Episode ID: {episode.get('id', '')}",
        f"Airdate: {airdate}",
        f"Title: {_episode_title(episode)}",
        f"Known main-story episodes: {episode_count}",
        "Note: Bangumi episode airdate has date precision only; exact broadcast time is not available.",
    ]
    return "\n".join(lines)


async def run(
    subject_id: str = "",
    as_of_date: str = "",
    **kwargs,
) -> str:
    """Fetch the latest aired main-story episode for a Bangumi subject.

    Args:
        subject_id: Bangumi subject ID (required).
        as_of_date: Optional reference date in YYYY-MM-DD format; defaults to today.
    """
    if not subject_id:
        return "Error: 'subject_id' parameter is required."

    try:
        parsed_subject_id = int(subject_id)
    except ValueError:
        return "Error: 'subject_id' must be an integer."

    try:
        as_of = _parse_as_of_date(as_of_date)
    except ValueError as exc:
        return f"Error: {exc}"

    client = BangumiClient(access_token=config.bangumi_token)
    try:
        subject = await client.fetch_subject(parsed_subject_id)
        episodes = await client.fetch_subject_episodes(
            parsed_subject_id, episode_type=0
        )
    except Exception as e:
        return f"Error fetching latest episode for subject {subject_id}: {e}"
    finally:
        await client.close()

    name = _subject_name(subject, subject_id)
    latest = _find_latest_aired_episode(episodes, as_of)
    if latest is None:
        return _format_no_episode(name, as_of, len(episodes))
    return _format_latest_episode(name, latest, as_of, len(episodes))
