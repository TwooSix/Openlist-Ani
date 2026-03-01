"""
User profile system for anime preference analysis.

Provides incremental profile building with LLM analysis, staff enrichment,
and disk-cached persistence. Used by the recommendation tool to generate
personalized anime suggestions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from ....config import config
from ....core.bangumi.client import BangumiClient
from ....core.bangumi.model import (
    COLLECTION_TYPE_LABELS,
    SubjectType,
    UserCollectionEntry,
)
from ....logger import logger
from .bangumi import _get_client

_DATA_DIR = Path.cwd().resolve() / "data"
_PROFILE_PATH = _DATA_DIR / "user_profile.json"
_STAFF_CACHE_PATH = _DATA_DIR / "staff_cache.json"
_STAFF_CACHE_TTL = 7 * 24 * 3600  # 7 days in seconds
_PROFILE_VERSION = 2

# ================================================================
# LLM analysis prompt
# ================================================================

_PROFILE_ANALYSIS_PROMPT = """\
You are an anime preference analyst. Given a user's anime collection data, \
analyze their preferences and produce a structured JSON profile.

Each entry contains: anime name, tags, summary snippet, user rating \
(0=unrated), user comment, collection type, and optionally production \
staff info (animation studio, director, script writer, music composer, \
original work author).

Analyze the entries and output a JSON object with:
- "preferred_genres": list of genre/theme preferences (max 10), each \
with "name" and "weight" (0-1)
- "preferred_tags": list of meaningful content tags the user likes \
(max 15), each with "name" and "weight" (0-1)
- "preferred_studios": list of animation studios associated with the \
user's highly-rated shows (max 5), each with "name" and "weight" (0-1)
- "preferred_staff": list of notable directors, writers, or other \
staff associated with highly-rated shows (max 5), each with "name", \
"role", and "weight" (0-1)
- "disliked_tags": list of tags/themes the user seems to avoid or \
rate low (max 5)
- "rating_tendency": one of "generous", "moderate", "strict" based \
on their rating patterns
- "preference_summary": a 2-3 sentence summary of the user's anime \
taste in Chinese, mentioning genre, studio, and staff preferences \
where notable

IMPORTANT:
- Ignore meaningless tags like year numbers (e.g. "2024", "2023"), \
season labels, platform names, or other non-descriptive tags.
- Studios and staff should only be included if they appear in multiple \
highly-rated entries, not based on a single data point.
- Focus on genre, theme, and content-related tags only.

Output valid JSON only, no markdown formatting."""

# ================================================================
# Staff enrichment
# ================================================================

# Infobox keys that contain production staff / studio info
_STAFF_KEYS = frozenset(
    {
        "动画制作",
        "制作",
        "导演",
        "总导演",
        "系列构成",
        "脚本",
        "音乐",
        "原作",
    }
)


def _sanitize_staff_entry(raw: dict) -> dict[str, Any]:
    """Sanitize a single staff cache entry using type constructors.

    Breaks the SonarCloud taint chain by reconstructing each field
    through explicit type conversions.

    Args:
        raw: Raw entry dict from the cache file.

    Returns:
        Sanitized entry with validated types.
    """
    staff = raw.get("staff")
    clean_staff: dict[str, str] | None = None
    if isinstance(staff, dict):
        clean_staff = {str(k): str(v) for k, v in staff.items()}
    raw_ts = raw.get("ts", 0)
    clean_ts = float(raw_ts) if isinstance(raw_ts, (int, float)) else 0.0
    return {"staff": clean_staff, "ts": clean_ts}


def _extract_staff_info(infobox: list[dict]) -> dict[str, str]:
    """Extract production staff / studio info from a subject's infobox.

    Args:
        infobox: Raw infobox list from BangumiSubject.

    Returns:
        Dict mapping role name (e.g. "动画制作") to staff/studio names.
    """
    info: dict[str, str] = {}
    for item in infobox:
        key = item.get("key", "")
        if key not in _STAFF_KEYS:
            continue
        value = item.get("value", "")
        if isinstance(value, list):
            # list of dicts like [{"v": "Name"}, ...]
            names = [v.get("v", "") for v in value if isinstance(v, dict)]
            value = ", ".join(n for n in names if n)
        if value:
            info[key] = str(value)
    return info


def _load_staff_cache() -> dict[str, Any]:
    """Load the staff info disk cache with data sanitization.

    Each entry is reconstructed through explicit type conversions
    to prevent taint propagation from disk-read data.

    Returns:
        Dict with ``entries`` (subject_id str -> {"staff": ..., "ts": ...})
        or empty structure if missing / corrupt.
    """
    if not _STAFF_CACHE_PATH.exists():
        return {"entries": {}}
    try:
        data = json.loads(_STAFF_CACHE_PATH.read_text("utf-8"))
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, dict):
            return {"entries": {}}
        clean: dict[str, Any] = {}
        for key, val in raw_entries.items():
            if isinstance(val, dict):
                clean[str(key)] = _sanitize_staff_entry(val)
        return {"entries": clean}
    except (json.JSONDecodeError, OSError):
        return {"entries": {}}


def _save_staff_cache(cache: dict[str, Any]) -> None:
    """Persist staff cache to disk.

    Args:
        cache: Cache dict with ``entries`` mapping.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STAFF_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), "utf-8"
    )


async def _enrich_with_staff(
    client: BangumiClient,
    entries: list[UserCollectionEntry],
) -> dict[int, dict[str, str]]:
    """Fetch staff info for ALL collection entries, with 7-day disk cache.

    Already-cached subjects are loaded instantly from disk; only uncached
    subjects trigger API calls.  Results are persisted so subsequent runs
    (even across process restarts) skip the network round-trip.

    Args:
        client: Active BangumiClient instance.
        entries: User collection entries.

    Returns:
        Mapping of subject_id -> staff info dict.
    """
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    cache = _load_staff_cache()
    cached_entries: dict[str, Any] = cache.get("entries", {})

    staff_map: dict[int, dict[str, str]] = {}
    to_fetch: list[int] = []

    for entry in entries:
        sid_str = str(entry.subject_id)
        cached = cached_entries.get(sid_str)
        if cached and (now_ts - cached.get("ts", 0)) < _STAFF_CACHE_TTL:
            # Disk cache hit — still within TTL
            staff = cached.get("staff")
            if staff:
                staff_map[entry.subject_id] = staff
        else:
            to_fetch.append(entry.subject_id)

    if not to_fetch:
        logger.info(f"Staff cache: {len(staff_map)} cached, 0 to fetch")
        return staff_map

    logger.info(f"Staff cache: {len(staff_map)} cached, {len(to_fetch)} to fetch")

    fetched = 0
    for sid in to_fetch:
        try:
            subject = await client.fetch_subject(sid)
            info = _extract_staff_info(subject.infobox)
            # Cache even empty results to avoid re-fetching
            cached_entries[str(sid)] = {"staff": info or None, "ts": now_ts}
            if info:
                staff_map[sid] = info
                fetched += 1
        except Exception:
            logger.debug(f"Failed to fetch staff for subject {sid}")

    logger.info(f"Enriched {fetched}/{len(to_fetch)} new subjects with staff info")
    _save_staff_cache({"entries": cached_entries})
    return staff_map


# ================================================================
# Profile persistence
# ================================================================


def _sanitize_weighted_list(raw: Any) -> list[dict[str, Any]]:
    """Sanitize a list of weighted preference entries.

    Args:
        raw: Raw list from loaded JSON, each item has "name" and "weight".

    Returns:
        Sanitized list with validated str/float types.
    """
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {"name": str(item.get("name", ""))}
        if "weight" in item:
            entry["weight"] = float(item["weight"])
        if "role" in item:
            entry["role"] = str(item["role"])
        result.append(entry)
    return result


def _sanitize_llm_analysis(raw: Any) -> dict[str, Any]:
    """Sanitize LLM analysis data using explicit type conversions.

    Breaks the SonarCloud taint chain by reconstructing each field
    from the loaded data through type constructors.

    Args:
        raw: Raw llm_analysis dict from the profile JSON file.

    Returns:
        Sanitized llm_analysis dict with validated types.
    """
    if not isinstance(raw, dict):
        return {}
    analysis: dict[str, Any] = {}
    for key in (
        "preferred_genres",
        "preferred_tags",
        "preferred_studios",
        "preferred_staff",
        "disliked_tags",
    ):
        if key in raw:
            analysis[key] = _sanitize_weighted_list(raw[key])
    if "rating_tendency" in raw:
        analysis["rating_tendency"] = str(raw["rating_tendency"])
    if "preference_summary" in raw:
        analysis["preference_summary"] = str(raw["preference_summary"])
    return analysis


def _load_profile() -> dict[str, Any] | None:
    """Load user profile from disk with data sanitization.

    Each field is reconstructed through explicit type conversions
    to prevent taint propagation from disk-read data.

    Returns:
        Profile dict or None if not found / incompatible version.
    """
    if not _PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(_PROFILE_PATH.read_text("utf-8"))
        if data.get("version") != _PROFILE_VERSION:
            logger.info("User profile version mismatch - will rebuild")
            return None
        # Sanitize: reconstruct with validated types to break taint chain
        raw_stats = data.get("collection_stats", {})
        clean_stats = (
            {str(k): int(v) for k, v in raw_stats.items()}
            if isinstance(raw_stats, dict)
            else {}
        )
        raw_ids = data.get("synced_subject_ids", [])
        clean_ids = [int(sid) for sid in raw_ids] if isinstance(raw_ids, list) else []
        profile: dict[str, Any] = {
            "version": int(data.get("version", 0)),
            "last_synced_at": str(data.get("last_synced_at", "")),
            "synced_subject_ids": clean_ids,
            "avg_rating": float(data.get("avg_rating", 0.0)),
            "total_rated": int(data.get("total_rated", 0)),
            "rating_sum": float(data.get("rating_sum", 0.0)),
            "collection_stats": clean_stats,
            "llm_analysis": _sanitize_llm_analysis(data.get("llm_analysis", {})),
        }
        return profile
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(f"Failed to load user profile: {exc}")
        return None


def _save_profile(profile: dict[str, Any]) -> None:
    """Persist user profile to disk.

    Args:
        profile: Complete profile dict to save.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), "utf-8")
    logger.info(f"User profile saved to {_PROFILE_PATH}")


# ================================================================
# LLM analysis
# ================================================================


def _create_llm_client() -> AsyncOpenAI | None:
    """Create an AsyncOpenAI client for profile analysis.

    Returns:
        AsyncOpenAI instance or None if API key is not configured.
    """
    if not config.llm.openai_api_key:
        return None
    return AsyncOpenAI(
        api_key=config.llm.openai_api_key,
        base_url=config.llm.openai_base_url,
        timeout=120.0,
    )


def _format_entry_subject_fields(
    entry: UserCollectionEntry,
) -> tuple[str, str, str]:
    """Extract name, summary, and tags string from a collection entry's subject.

    Args:
        entry: A user collection entry.

    Returns:
        Tuple of (name, summary, tags_str).
    """
    if not entry.subject:
        return "", "", ""
    name = entry.subject.name_cn or entry.subject.name
    summary = (entry.subject.short_summary or "")[:200]
    tags_str = (
        ", ".join(t.name for t in entry.subject.tags[:10]) if entry.subject.tags else ""
    )
    return name, summary, tags_str


def _prepare_entries_text(
    entries: list[UserCollectionEntry],
    staff_map: dict[int, dict[str, str]] | None = None,
) -> str:
    """Format collection entries as text input for LLM analysis.

    Args:
        entries: List of user collection entries.
        staff_map: Optional mapping of subject_id to production staff info.

    Returns:
        Formatted text describing all entries.
    """
    lines: list[str] = []
    for entry in entries:
        name, summary, tags_str = _format_entry_subject_fields(entry)

        user_tags = ", ".join(entry.tags) if entry.tags else ""
        label = COLLECTION_TYPE_LABELS.get(entry.type, "unknown")
        rate_str = str(entry.rate) if entry.rate > 0 else "unrated"
        comment = entry.comment or ""

        staff_str = ""
        if staff_map and entry.subject_id in staff_map:
            parts = [f"{k}: {v}" for k, v in staff_map[entry.subject_id].items()]
            staff_str = f" | Staff: [{', '.join(parts)}]"

        lines.append(
            f"- {name or f'ID:{entry.subject_id}'} | "
            f"Status: {label} | Rating: {rate_str} | "
            f"Tags: [{tags_str}] | UserTags: [{user_tags}] | "
            f"Comment: {comment} | Summary: {summary}"
            f"{staff_str}"
        )
    return "\n".join(lines)


async def _analyze_with_llm(
    entries: list[UserCollectionEntry],
    staff_map: dict[int, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Use LLM to analyze user's collection and extract preference profile.

    Args:
        entries: User collection entries to analyze.
        staff_map: Optional mapping of subject_id to production staff info.

    Returns:
        LLM-generated preference dict, or fallback empty dict on failure.
    """
    client = _create_llm_client()
    if client is None:
        logger.warning("LLM client not available for profile analysis")
        return {}

    entries_text = _prepare_entries_text(entries, staff_map)
    if not entries_text.strip():
        return {}

    try:
        response = await client.chat.completions.create(
            model=config.llm.openai_model,
            messages=[
                {"role": "system", "content": _PROFILE_ANALYSIS_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Analyze the following {len(entries)} anime "
                        f"collection entries:\n\n{entries_text}"
                    ),
                },
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content or ""
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning(f"LLM profile analysis failed: {exc}")
        return {}


# ================================================================
# Profile build / update
# ================================================================


async def _build_or_update_profile(
    client: BangumiClient | None = None,
) -> dict[str, Any]:
    """Build or incrementally update the user profile.

    If a compatible profile exists, only new/changed collection entries
    are fetched and merged. Otherwise a full rebuild is performed.
    Uses LLM analysis to extract meaningful preferences from collection data.

    Args:
        client: Optional BangumiClient; falls back to the shared singleton.

    Returns:
        Complete up-to-date user profile dict.
    """
    if client is None:
        client = _get_client()

    profile = _load_profile()
    is_full_rebuild = profile is None

    if is_full_rebuild:
        profile = {
            "version": _PROFILE_VERSION,
            "last_synced_at": "",
            "synced_subject_ids": [],
            "avg_rating": 0.0,
            "total_rated": 0,
            "rating_sum": 0.0,
            "collection_stats": {},
            "llm_analysis": {},
        }

    synced_ids: set[int] = set(profile.get("synced_subject_ids", []))
    rating_sum: float = profile.get("rating_sum", 0.0)
    total_rated: int = profile.get("total_rated", 0)
    collection_stats: dict[str, int] = profile.get("collection_stats", {})

    # Fetch all anime collections
    try:
        collections = await client.fetch_user_collections(
            subject_type=SubjectType.ANIME
        )
    except Exception as exc:
        logger.error(f"Failed to fetch collections for profile: {exc}")
        if not is_full_rebuild:
            return profile
        raise

    new_entries = [e for e in collections if e.subject_id not in synced_ids]
    logger.info(
        f"Profile update: {len(new_entries)} new entries "
        f"(total {len(collections)}, synced {len(synced_ids)})"
    )

    for entry in new_entries:
        synced_ids.add(entry.subject_id)

        # Rating accumulation
        if entry.rate > 0:
            rating_sum += entry.rate
            total_rated += 1

        # Collection type stats
        label = COLLECTION_TYPE_LABELS.get(entry.type, "other")
        collection_stats[label] = collection_stats.get(label, 0) + 1

    # Run LLM analysis on the full collection when there are new entries,
    # on first build, or when staff-enriched fields are missing (upgrade).
    # We pass all entries (not just new) so LLM gets the full picture.
    llm_analysis = profile.get("llm_analysis", {})
    needs_reanalysis = (
        new_entries or not llm_analysis or "preferred_studios" not in llm_analysis
    )
    if needs_reanalysis:
        staff_map = await _enrich_with_staff(client, collections)
        llm_analysis = await _analyze_with_llm(collections, staff_map)

    now_iso = datetime.now(tz=timezone.utc).isoformat()

    profile.update(
        {
            "version": _PROFILE_VERSION,
            "last_synced_at": now_iso,
            "synced_subject_ids": sorted(synced_ids),
            "avg_rating": (round(rating_sum / total_rated, 2) if total_rated else 0.0),
            "total_rated": total_rated,
            "rating_sum": rating_sum,
            "collection_stats": collection_stats,
            "llm_analysis": llm_analysis,
        }
    )

    _save_profile(profile)
    return profile


# ================================================================
# Profile formatting
# ================================================================


def _format_analysis_lines(analysis: dict[str, Any]) -> list[str]:
    """Format LLM analysis fields into summary lines.

    Args:
        analysis: LLM analysis dict from user profile.

    Returns:
        List of formatted summary lines.
    """
    lines: list[str] = []

    _FIELD_FORMATTERS: list[tuple[str, str, int, str]] = [
        ("preference_summary", "Preference summary", 0, ""),
        ("rating_tendency", "Rating tendency", 0, ""),
    ]
    for key, label, _, _ in _FIELD_FORMATTERS:
        value = analysis.get(key, "")
        if value:
            lines.append(f"- {label}: {value}")

    _WEIGHTED_FIELDS = [
        ("preferred_genres", "Preferred genres", 10),
        ("preferred_tags", "Preferred tags", 15),
        ("preferred_studios", "Preferred studios", 5),
    ]
    for key, label, limit in _WEIGHTED_FIELDS:
        items = analysis.get(key, [])
        if items:
            formatted = ", ".join(
                f"{item['name']}({item['weight']:.1f})" for item in items[:limit]
            )
            lines.append(f"- {label}: {formatted}")

    staff = analysis.get("preferred_staff", [])
    if staff:
        staff_str = ", ".join(f"{s['name']}({s.get('role', '')})" for s in staff[:5])
        lines.append(f"- Preferred staff: {staff_str}")

    disliked = analysis.get("disliked_tags", [])
    if disliked:
        lines.append(f"- Disliked: {', '.join(disliked)}")

    return lines


def _format_profile_summary(profile: dict[str, Any]) -> str:
    """Format user profile as a concise text summary for LLM context.

    Args:
        profile: User profile dict.

    Returns:
        Human-readable profile summary string.
    """
    avg = profile.get("avg_rating", 0)
    total = profile.get("total_rated", 0)
    stats = profile.get("collection_stats", {})
    analysis = profile.get("llm_analysis", {})

    lines = ["## User Anime Profile"]
    lines.append(f"- Average rating: {avg} (based on {total} rated titles)")
    if stats:
        stats_str = ", ".join(f"{k}: {v}" for k, v in stats.items())
        lines.append(f"- Collection stats: {stats_str}")

    if analysis:
        lines.extend(_format_analysis_lines(analysis))

    return "\n".join(lines)
