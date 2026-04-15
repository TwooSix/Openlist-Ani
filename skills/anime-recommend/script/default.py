"""Check taste profile; auto-build with incremental caching + confidence.

One-shot entry point: returns taste profile analysis **and** Bayesian-
scored calendar candidates in a single call, minimizing LLM round-trips.

Uses local JSON caches so only changed entries trigger API fetches.
Subject detail fetches run concurrently (semaphore-limited).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from loguru import logger

from openlist_ani.config import config
from openlist_ani.core.bangumi.client import BangumiClient
from openlist_ani.core.bangumi.model import UserCollectionEntry


# ------------------------------------------------------------------ #
# Sibling module loader (skill loader doesn't support relative imports)
# ------------------------------------------------------------------ #

def _load_sibling_module(filename: str, module_name: str):
    """Import a sibling .py from the same script directory."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).parent / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_cache = _load_sibling_module("_cache.py", "_anime_recommend_cache")
CachedSubject = _cache.CachedSubject
build_collection_cache = _cache.build_collection_cache
diff_collections = _cache.diff_collections
load_collection_cache = _cache.load_collection_cache
load_subject_cache = _cache.load_subject_cache
save_collection_cache = _cache.save_collection_cache
save_subject_cache = _cache.save_subject_cache
subject_to_cached = _cache.subject_to_cached

_confidence = _load_sibling_module(
    "_confidence.py", "_anime_recommend_confidence",
)
compute_confidence = _confidence.compute_confidence
format_confidence_report = _confidence.format_confidence_report

_score = _load_sibling_module(
    "score_candidates.py", "_anime_recommend_score",
)
_parse_calendar_items = _score._parse_calendar_items
_compute_bayesian_scores = _score._compute_bayesian_scores
_format_results = _score._format_results


# ------------------------------------------------------------------ #
# Path helpers
# ------------------------------------------------------------------ #

def _resolve_memory_dir() -> Path:
    """Resolve the memory directory path."""
    return Path(config.assistant.data_dir) / "memory"


def _read_taste_profile() -> str:
    """Read anime_taste.md from memory directory."""
    taste_file = _resolve_memory_dir() / "anime_taste.md"
    if taste_file.is_file():
        return taste_file.read_text(encoding="utf-8").strip()
    return ""


def _display_name(name_cn: str | None, name: str | None) -> str:
    """Build a display name, preferring Chinese name when available."""
    if name_cn and name:
        return f"{name_cn} ({name})"
    return name_cn or name or ""


# ------------------------------------------------------------------ #
# Full-range selection (no top-N sampling)
# ------------------------------------------------------------------ #

def _select_entries_by_rating(
    all_entries: list[UserCollectionEntry],
) -> tuple[
    list[UserCollectionEntry],  # liked (rate >= 7)
    list[UserCollectionEntry],  # disliked (rate < 5 or dropped)
]:
    """Split collection entries into liked and disliked sets.

    Uses all entries — no top-N sampling:
    - Liked: rate >= 7 (sorted by rate descending)
    - Disliked: rate < 5 OR dropped (type == 5) with no high rate
    """
    liked = sorted(
        [e for e in all_entries if e.rate and e.rate >= 7],
        key=lambda e: e.rate,
        reverse=True,
    )
    # Disliked: explicit low ratings + dropped without a high rating
    low_rated = [e for e in all_entries if e.rate and e.rate < 5]
    low_ids = {e.subject_id for e in low_rated}
    liked_ids = {e.subject_id for e in liked}
    dropped = [
        e for e in all_entries
        if e.type == 5
        and e.subject_id not in low_ids
        and e.subject_id not in liked_ids
    ]
    disliked = low_rated + dropped
    return liked, disliked


# ------------------------------------------------------------------ #
# Titles list formatter (compact, for LLM context)
# ------------------------------------------------------------------ #

def _format_liked_entry(
    entry: UserCollectionEntry,
    subject_cache: dict[int, CachedSubject],
) -> str | None:
    """Format a single liked entry, or None if uncached."""
    cs = subject_cache.get(entry.subject_id)
    if not cs:
        return None
    name = _display_name(cs.name_cn, cs.name)
    line = f"- {name} — {entry.rate}/10"
    if entry.comment:
        line += f" | \"{entry.comment}\""
    if entry.tags:
        line += f" | tags: {', '.join(entry.tags)}"
    return line


def _format_disliked_label(entry: UserCollectionEntry) -> str:
    """Build the status label for a disliked/dropped entry."""
    if entry.type == 5 and not entry.rate:
        return "dropped"
    if entry.type == 5:
        return f"dropped, {entry.rate}/10"
    return f"{entry.rate}/10"


def _format_disliked_entry(
    entry: UserCollectionEntry,
    subject_cache: dict[int, CachedSubject],
) -> str | None:
    """Format a single disliked/dropped entry, or None if uncached."""
    cs = subject_cache.get(entry.subject_id)
    if not cs:
        return None
    name = _display_name(cs.name_cn, cs.name)
    label = _format_disliked_label(entry)
    line = f"- {name} — {label}"
    if entry.comment:
        line += f" | \"{entry.comment}\""
    return line


def _format_titles_list(
    liked: list[UserCollectionEntry],
    disliked: list[UserCollectionEntry],
    subject_cache: dict[int, CachedSubject],
) -> str:
    """Format a compact titles list with ratings and user comments.

    Gives the LLM concrete title context alongside the statistical
    confidence report.  Only includes titles whose subject details
    are cached.
    """
    parts: list[str] = []

    if liked:
        parts.append("## Liked Titles")
        for entry in liked:
            line = _format_liked_entry(entry, subject_cache)
            if line:
                parts.append(line)

    if disliked:
        parts.append("\n## Disliked / Dropped Titles")
        for entry in disliked:
            line = _format_disliked_entry(entry, subject_cache)
            if line:
                parts.append(line)

    return "\n".join(parts)


def _collect_unique_ids(
    *entry_lists: list[UserCollectionEntry],
) -> list[int]:
    """Collect unique subject IDs from multiple entry lists."""
    ids: list[int] = []
    seen: set[int] = set()
    for entries in entry_lists:
        for entry in entries:
            sid = entry.subject_id
            if sid and sid not in seen:
                ids.append(sid)
                seen.add(sid)
    return ids


# ------------------------------------------------------------------ #
# Core: incremental fetch + confidence report
# ------------------------------------------------------------------ #

async def _fetch_all_collections(
    client: BangumiClient,
) -> list[UserCollectionEntry]:
    """Fetch done + doing + dropped collections."""
    all_entries: list[UserCollectionEntry] = []
    for ct in (2, 3, 5):  # done, doing, dropped
        entries = await client.fetch_user_collections(
            subject_type=2, collection_type=ct,
        )
        all_entries.extend(entries)
    return all_entries


# Maximum concurrent fetch_subject requests.  Bangumi API tolerates
# moderate concurrency; 5 parallel requests with the 0.5s per-request
# throttle effectively reduces wall-clock time ~5×.
_FETCH_CONCURRENCY = 5


async def _ensure_subjects_cached(
    client: BangumiClient,
    needed_ids: list[int],
    subject_cache: dict[int, CachedSubject],
) -> int:
    """Fetch and cache subject details concurrently.

    Uses a semaphore to limit concurrent API requests.
    Returns the number of API calls made.
    """
    missing_ids = [sid for sid in needed_ids if sid not in subject_cache]
    if not missing_ids:
        return 0

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
    fetch_count = 0

    async def _fetch_one(sid: int) -> None:
        nonlocal fetch_count
        async with sem:
            try:
                detail = await client.fetch_subject(sid)
                subject_cache[sid] = subject_to_cached(detail)
                fetch_count += 1
            except Exception as e:
                logger.warning(f"Failed to fetch subject {sid}: {e}")

    await asyncio.gather(*[_fetch_one(sid) for sid in missing_ids])
    return fetch_count


async def _incremental_fetch_and_build(
    client: BangumiClient,
) -> tuple[str, str, bool]:
    """Fetch collections incrementally, build confidence report.

    Uses local JSON caches to minimize API calls:
    1. Fetch collection list (always — it's a single paginated request)
    2. Diff against cached collection snapshots
    3. Only fetch subject details for new/changed entries
    4. Compute Bayesian confidence statistics via ``_confidence.py``

    Returns:
        (existing_profile, analysis_data, has_changes)
        - When no changes: existing_profile is set, analysis_data is ""
        - When changes exist: analysis_data is set, existing_profile is ""
    """
    # 1. Fetch current collections
    all_entries = await _fetch_all_collections(client)
    if not all_entries:
        return "", "", False

    # 2. Diff against cache
    old_collection_cache = load_collection_cache()
    changed_ids, removed_ids = diff_collections(old_collection_cache, all_entries)

    # 3. No changes + profile exists -> skip rebuild
    existing_profile = _read_taste_profile()
    if not changed_ids and not removed_ids and existing_profile:
        logger.info("No collection changes detected, using existing profile")
        return existing_profile, "", False

    # 4. Full-range selection (no top-N sampling)
    liked, disliked = _select_entries_by_rating(all_entries)
    all_needed_ids = _collect_unique_ids(liked, disliked)

    # 5. Load subject cache, fetch only missing details
    subject_cache = load_subject_cache()
    fetch_count = await _ensure_subjects_cached(
        client, all_needed_ids, subject_cache,
    )
    if fetch_count > 0:
        save_subject_cache(subject_cache)
        logger.info(
            f"Fetched {fetch_count} new subject details, "
            f"cache now has {len(subject_cache)} entries",
        )

    # 6. Update collection cache
    new_collection_cache = build_collection_cache(all_entries)
    save_collection_cache(new_collection_cache)

    # 7. Compute Bayesian confidence report
    liked_subjects = [
        subject_cache[e.subject_id]
        for e in liked
        if e.subject_id in subject_cache
    ]
    disliked_subjects = [
        subject_cache[e.subject_id]
        for e in disliked
        if e.subject_id in subject_cache
    ]
    report = compute_confidence(liked_subjects, disliked_subjects)
    confidence_text = format_confidence_report(report)

    # 8. Format titles list (compact, for LLM context)
    titles_text = _format_titles_list(liked, disliked, subject_cache)

    # 9. Assemble output
    rated_count = sum(1 for e in all_entries if e.rate)
    overview = (
        f"## Collection Overview\n"
        f"Total entries: {len(all_entries)}, "
        f"rated: {rated_count}, "
        f"liked (≥7): {len(liked)}, "
        f"disliked (<5 or dropped): {len(disliked)}\n"
    )
    analysis_data = f"{overview}\n{confidence_text}\n---\n\n{titles_text}"

    has_changes = bool(changed_ids or removed_ids or not existing_profile)
    return "", analysis_data, has_changes


# ------------------------------------------------------------------ #
# Calendar fetch + Bayesian scoring (inline, zero extra API overhead)
# ------------------------------------------------------------------ #

def _format_calendar_item(item) -> str:
    """Format a calendar item matching ``calendar.py._format_item``."""
    name_cn = item.name_cn or ""
    name = item.name or ""
    display = f"{name_cn} ({name})" if name_cn and name else name_cn or name
    score_str = f" score:{item.rating.score}" if item.rating.score else ""
    votes_str = f" votes:{item.rating.total}" if item.rating.total else ""
    rank_str = f" rank:#{item.rank}" if item.rank else ""
    return f"  - [ID:{item.id}] {display}{score_str}{votes_str}{rank_str}"


async def _fetch_and_score_calendar(
    client: BangumiClient,
    collection_ids: set[int],
) -> str:
    """Fetch calendar, score candidates, return formatted output.

    Args:
        client: Active BangumiClient.
        collection_ids: Subject IDs already in user's collection
            (to flag in output).

    Returns:
        Formatted calendar + Bayesian scoring text, or empty string on
        failure.
    """
    try:
        days = await client.fetch_calendar()
    except Exception as e:
        logger.warning(f"Failed to fetch calendar: {e}")
        return ""

    if not days:
        return ""

    # Build calendar text (same format as bangumi/calendar skill)
    cal_lines = ["# Weekly Anime Calendar\n"]
    for day in days:
        weekday_name = day.weekday.cn or day.weekday.en or f"Day {day.weekday.id}"
        cal_lines.append(f"## {weekday_name}")
        if not day.items:
            cal_lines.append("  (no anime)")
        else:
            for item in day.items:
                cal_lines.append(_format_calendar_item(item))
        cal_lines.append("")

    calendar_text = "\n".join(cal_lines)

    # Score candidates using the calendar text
    candidates = _parse_calendar_items(calendar_text)
    if not candidates:
        return calendar_text

    # Mark titles already in user's collection
    for c in candidates:
        c["in_collection"] = c["id"] in collection_ids

    scored = _compute_bayesian_scores(candidates)
    scoring_text = _format_results(scored)

    return f"{calendar_text}\n---\n\n{scoring_text}"


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

async def run(**kwargs) -> str:
    """Build taste profile and score calendar candidates in one call.

    Returns everything the LLM needs to make recommendations:
    - Taste profile (existing or new confidence analysis)
    - Calendar data with Bayesian-scored candidates

    This eliminates separate bangumi/calendar and score_candidates calls.
    """
    token = config.bangumi_token
    if not token:
        return (
            "No taste profile and no Bangumi token.\n"
            "Give objective recommendations only:\n"
            "1. Call bangumi/calendar\n"
            "2. Call anime-recommend/score_candidates with the output\n"
            "3. Note to user: based on community ratings, not personal taste"
        )

    client = BangumiClient(access_token=token)
    try:
        existing_profile, analysis_data, has_changes = (
            await _incremental_fetch_and_build(client)
        )

        # Collect user's collection IDs for duplicate flagging
        all_entries = await _fetch_all_collections(client)
        collection_ids = {e.subject_id for e in all_entries if e.subject_id}

        # Fetch calendar + score candidates in the same client session
        calendar_scored = await _fetch_and_score_calendar(
            client, collection_ids,
        )
    except Exception as e:
        logger.error(f"Error building taste profile: {e}")
        return (
            f"Error fetching Bangumi data: {e}\n"
            "Fall back to objective recommendations."
        )
    finally:
        await client.close()

    # ---- Assemble output ----

    parts: list[str] = []

    # Part 1: Taste profile
    if not has_changes and existing_profile:
        parts.append(
            "## Taste Profile (up-to-date)\n\n"
            f"{existing_profile}"
        )
    elif analysis_data:
        parts.append(
            "**ACTION REQUIRED — do this BEFORE reading Part 2.**\n\n"
            "## Part 1: Taste Profile (NEW)\n\n"
            f"{analysis_data}\n\n"
            "---\n"
            "**STOP.** Call "
            "`memory(action=\"write\", filename=\"anime_taste.md\")` "
            "now to save the profile above.\n\n"
            "**Rules:**\n"
            "- ONLY transcribe genres/studios/directors from the "
            "confidence report sections above.\n"
            "- If a section (e.g. Disliked Genres) is missing or "
            "empty in the report, do NOT write it in the profile.\n"
            "- Do NOT infer preferences from the titles list — "
            "titles are context, not the source of truth.\n"
            "- Use the tiered format from SKILL.md "
            "(Strong/Weak/Disliked/Titles).\n\n"
            "After saving, continue to Part 2 below."
        )
    else:
        parts.append(
            "## Taste Profile\n\n"
            "No rated collection data. Recommend based on scores only."
        )

    # Part 2: Calendar + scored candidates
    if calendar_scored:
        parts.append(f"\n{'=' * 60}\n\n## Part 2: Scored Candidates\n\n"
                      f"{calendar_scored}")
    else:
        parts.append(
            "\n---\n\nCalendar fetch failed. "
            "Call bangumi/calendar manually."
        )

    return "\n".join(parts)
