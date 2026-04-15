"""Local JSON cache for user collections and subject details.

Provides incremental diff detection so only changed entries need
re-fetching from the Bangumi API.

Cache files live under ``data/assistant/cache/anime-recommend/``:
- ``collections_cache.json`` — snapshot of user collection entries
- ``subjects_cache.json``    — fetched BangumiSubject details
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from openlist_ani.config import config
from openlist_ani.core.bangumi.model import BangumiSubject, UserCollectionEntry


# ------------------------------------------------------------------ #
# Cache directory
# ------------------------------------------------------------------ #

def _cache_dir() -> Path:
    """Resolve the cache directory, creating it if needed."""
    d = Path(config.assistant.data_dir) / "cache" / "anime-recommend"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ #
# Collection snapshot
# ------------------------------------------------------------------ #

@dataclass
class CollectionSnapshot:
    """Minimal snapshot of a collection entry for diff comparison."""

    subject_id: int
    rate: int
    collection_type: int  # CollectionType value (1-5)
    comment: str
    tags: list[str] = field(default_factory=list)
    updated_at: str = ""
    # SlimSubject basics (for display without re-fetching)
    name: str = ""
    name_cn: str = ""


def _snapshot_from_entry(entry: UserCollectionEntry) -> CollectionSnapshot:
    """Build a snapshot from a live API entry."""
    return CollectionSnapshot(
        subject_id=entry.subject_id,
        rate=entry.rate,
        collection_type=entry.type,
        comment=entry.comment,
        tags=list(entry.tags),
        updated_at=entry.updated_at,
        name=entry.subject.name if entry.subject else "",
        name_cn=entry.subject.name_cn if entry.subject else "",
    )


def _snapshot_key(snap: CollectionSnapshot) -> tuple:
    """Return the comparison tuple for change detection."""
    return (
        snap.rate, snap.collection_type, snap.comment,
        tuple(snap.tags), snap.updated_at,
    )


# ------------------------------------------------------------------ #
# Subject detail cache
# ------------------------------------------------------------------ #

@dataclass
class CachedSubject:
    """Cached subset of BangumiSubject detail."""

    id: int
    name: str = ""
    name_cn: str = ""
    summary: str = ""
    date: str = ""
    score: float = 0.0
    vote_count: int = 0
    tags: list[dict[str, Any]] = field(default_factory=list)  # [{name, count}]
    infobox: list[dict[str, Any]] = field(default_factory=list)


def subject_to_cached(subj: BangumiSubject) -> CachedSubject:
    """Convert a BangumiSubject to its cached form."""
    return CachedSubject(
        id=subj.id,
        name=subj.name,
        name_cn=subj.name_cn,
        summary=subj.summary[:500] if subj.summary else "",
        date=subj.date,
        score=subj.rating.score if subj.rating else 0.0,
        vote_count=subj.rating.total if subj.rating else 0,
        tags=[{"name": t.name, "count": t.count} for t in subj.tags],
        infobox=subj.infobox,
    )


# ------------------------------------------------------------------ #
# JSON I/O
# ------------------------------------------------------------------ #

def load_collection_cache() -> dict[int, CollectionSnapshot]:
    """Load the collection snapshot cache from disk."""
    path = _cache_dir() / "collections_cache.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[int, CollectionSnapshot] = {}
        for item in raw:
            # Defensive: ignore unknown keys, use defaults for missing ones
            fields = CollectionSnapshot.__dataclass_fields__
            filtered = {k: v for k, v in item.items() if k in fields}
            snap = CollectionSnapshot(**filtered)
            result[snap.subject_id] = snap
        return result
    except Exception as e:
        logger.warning(f"Failed to load collection cache: {e}")
        return {}


def save_collection_cache(snapshots: dict[int, CollectionSnapshot]) -> None:
    """Save the collection snapshot cache to disk."""
    path = _cache_dir() / "collections_cache.json"
    data = [asdict(s) for s in snapshots.values()]
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.debug(f"Saved collection cache: {len(data)} entries")
    except Exception as e:
        logger.warning(f"Failed to save collection cache: {e}")


def load_subject_cache() -> dict[int, CachedSubject]:
    """Load the subject detail cache from disk."""
    path = _cache_dir() / "subjects_cache.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[int, CachedSubject] = {}
        for item in raw:
            fields = CachedSubject.__dataclass_fields__
            filtered = {k: v for k, v in item.items() if k in fields}
            cs = CachedSubject(**filtered)
            result[cs.id] = cs
        return result
    except Exception as e:
        logger.warning(f"Failed to load subject cache: {e}")
        return {}


def save_subject_cache(cache: dict[int, CachedSubject]) -> None:
    """Save the subject detail cache to disk."""
    path = _cache_dir() / "subjects_cache.json"
    data = [asdict(s) for s in cache.values()]
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.debug(f"Saved subject cache: {len(data)} entries")
    except Exception as e:
        logger.warning(f"Failed to save subject cache: {e}")


# ------------------------------------------------------------------ #
# Diff logic
# ------------------------------------------------------------------ #

def diff_collections(
    old: dict[int, CollectionSnapshot],
    current_entries: list[UserCollectionEntry],
) -> tuple[list[int], list[int]]:
    """Compare current API entries against cached snapshots.

    Args:
        old: Previously cached snapshots keyed by subject_id.
        current_entries: Fresh entries from the API.

    Returns:
        (changed_ids, removed_ids):
        - changed_ids: subject IDs that are new or modified
        - removed_ids: subject IDs that were in cache but no longer present
    """
    current_map: dict[int, CollectionSnapshot] = {}
    for entry in current_entries:
        snap = _snapshot_from_entry(entry)
        current_map[snap.subject_id] = snap

    changed: list[int] = []
    for sid, snap in current_map.items():
        old_snap = old.get(sid)
        if old_snap is None or _snapshot_key(old_snap) != _snapshot_key(snap):
            changed.append(sid)

    current_ids = set(current_map.keys())
    old_ids = set(old.keys())
    removed = list(old_ids - current_ids)

    return changed, removed


def build_collection_cache(entries: list[UserCollectionEntry]) -> dict[int, CollectionSnapshot]:
    """Build a fresh collection cache from API entries."""
    return {
        entry.subject_id: _snapshot_from_entry(entry)
        for entry in entries
        if entry.subject_id
    }
