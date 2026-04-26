"""Tag/studio/director confidence scoring for taste profile.

Extracts features from CachedSubject data and computes frequency-based
confidence scores.  Only features that appear in >= ``MIN_COUNT`` liked
(or disliked) titles are considered significant preferences.

Private module (``_`` prefix keeps it out of the skill catalog).
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ #
# Import CachedSubject from sibling _cache.py
# ------------------------------------------------------------------ #


def _load_cache_module():
    """Import _cache.py from the same script directory."""
    module_name = "_anime_recommend_cache"
    if module_name in sys.modules:
        return sys.modules[module_name]
    cache_path = Path(__file__).parent / "_cache.py"
    spec = importlib.util.spec_from_file_location(module_name, cache_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_cache = _load_cache_module()
CachedSubject = _cache.CachedSubject

# ------------------------------------------------------------------ #
# Constants
# ------------------------------------------------------------------ #

# Tags that carry no genre/style signal — filter them out
_TAG_BLACKLIST = frozenset(
    {
        # Media format
        "TV",
        "OVA",
        "ONA",
        "WEB",
        "剧场版",
        "短片",
        # Adaptation source
        "漫画改",
        "漫改",
        "小说改",
        "轻小说改",
        "轻改",
        "GAL改",
        "游戏改",
        "原创",
        # Status
        "续篇",
        "续作",
        "完结",
        "连载中",
        # Geography
        "日本",
        # Technical
        "3D",
        "CG",
        "真人",
    }
)

# Minimum appearances required to count as a preference
MIN_COUNT = 2

# Bayesian prior strength: how many observations before we trust the data.
# With m=3, a tag appearing 1 time is heavily shrunk toward the base rate,
# while a tag appearing 6+ times closely reflects its true frequency.
PRIOR_STRENGTH = 3

# Frequency thresholds for tiered preference classification.
# Applied uniformly to all dimensions (genres, studios, directors, writers).
STRONG_THRESHOLD = 0.50  # ≥50% of liked titles → strong preference
WEAK_THRESHOLD = 0.30  # 30-50% → weak preference
# <30% → ignored (not included in profile)

# Maximum tags to inspect per subject (Bangumi sorts by popularity)
_MAX_TAGS_PER_SUBJECT = 12

# Tags longer than this are likely title-specific, not genre descriptors
_MAX_TAG_NAME_LENGTH = 8

# Known studio names that appear as Bangumi tags.
# Anything matching these is reclassified from genre → studio.
_KNOWN_STUDIO_TAGS = frozenset(
    {
        "A-1Pictures",
        "A-1 Pictures",
        "MADHOUSE",
        "MADHouse",
        "MAPPA",
        "BONES",
        "ufotable",
        "CloverWorks",
        "Passione",
        "Production I.G",
        "WIT STUDIO",
        "WIT",
        "SHAFT",
        "P.A.WORKS",
        "TRIGGER",
        "京都アニメーション",
        "KyoAni",
        "サンライズ",
        "SUNRISE",
        "动画工房",
        "動画工房",
        "SILVER LINK.",
        "J.C.STAFF",
        "TOHO animation",
        "东映动画",
        "東映アニメーション",
        "东映",
        "CygamesPictures",
        "スタジオコロリド",
        "SANZIGEN",
        "STUDIO CHROMATO",
        "Studio Outrigger",
    }
)

# Alias map for studio name normalization (variant → canonical)
_STUDIO_ALIASES: dict[str, str] = {
    "A-1 Pictures": "A-1Pictures",
    "MADHouse": "MADHOUSE",
    "WIT": "WIT STUDIO",
    "KyoAni": "京都アニメーション",
    "SUNRISE": "サンライズ",
    "動画工房": "动画工房",
    "東映アニメーション": "东映动画",
    "东映": "东映动画",
}


def _normalize_studio(name: str) -> str:
    """Normalize studio name using alias map."""
    return _STUDIO_ALIASES.get(name, name)


# ------------------------------------------------------------------ #
# Feature extraction from a single CachedSubject
# ------------------------------------------------------------------ #


def _is_date_tag(name: str) -> bool:
    """Check if a tag looks like a date (e.g. '2024年4月', '2024')."""
    return any(c.isdigit() for c in name) and ("年" in name or name.isdigit())


def _is_likely_studio_tag(name: str) -> bool:
    """Heuristic: detect studio-like tags not in the explicit set."""
    lower = name.lower()
    return any(kw in lower for kw in ("studio", "pictures", "works"))


def _extract_genres(tags: list[dict[str, Any]]) -> list[str]:
    """Extract meaningful genre tags, excluding noise and studio names."""
    result: list[str] = []
    for tag in tags[:_MAX_TAGS_PER_SUBJECT]:
        name = tag.get("name", "")
        if (
            not name
            or len(name) > _MAX_TAG_NAME_LENGTH
            or name in _TAG_BLACKLIST
            or name in _KNOWN_STUDIO_TAGS
            or _is_date_tag(name)
            or _is_likely_studio_tag(name)
        ):
            continue
        result.append(name)
    return result


def _extract_studio_tags(tags: list[dict[str, Any]]) -> list[str]:
    """Extract and normalize studio names that appear in Bangumi tags."""
    result: list[str] = []
    for tag in tags[:_MAX_TAGS_PER_SUBJECT]:
        name = tag.get("name", "")
        if name in _KNOWN_STUDIO_TAGS:
            result.append(_normalize_studio(name))
    return result


def _extract_infobox_values(
    infobox: list[dict[str, Any]],
    target_keys: set[str],
) -> list[str]:
    """Extract names from infobox entries matching target keys."""
    result: list[str] = []
    for item in infobox:
        key = item.get("key", "")
        value = item.get("value", "")
        if key not in target_keys or not value:
            continue
        if isinstance(value, list):
            names = [
                v.get("v", "") for v in value if isinstance(v, dict) and v.get("v")
            ]
        else:
            names = [str(value)] if value else []
        result.extend(names)
    return result


@dataclass
class SubjectFeatures:
    """Extracted features from a single anime."""

    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)


def extract_features(cs: CachedSubject) -> SubjectFeatures:
    """Extract all feature dimensions from a CachedSubject."""
    # Studios: prefer infobox '动画制作', supplement with tag-based detection
    infobox_studios = [
        _normalize_studio(s) for s in _extract_infobox_values(cs.infobox, {"动画制作"})
    ]
    tag_studios = _extract_studio_tags(cs.tags)
    # Deduplicate, prefer infobox
    seen_studios: set[str] = set()
    studios: list[str] = []
    for s in (*infobox_studios, *tag_studios):
        if s not in seen_studios:
            studios.append(s)
            seen_studios.add(s)

    return SubjectFeatures(
        genres=_extract_genres(cs.tags),
        studios=studios,
        directors=_extract_infobox_values(
            cs.infobox,
            {"导演", "总导演"},
        ),
        writers=_extract_infobox_values(
            cs.infobox,
            {"系列构成", "脚本"},
        ),
    )


# ------------------------------------------------------------------ #
# Confidence scoring
# ------------------------------------------------------------------ #


@dataclass
class ConfidenceEntry:
    """A single tag/studio/director with Bayesian-averaged frequency.

    Uses Bayesian averaging to shrink observed frequency toward a base
    rate, preventing low-count tags from ranking high on noise alone.

    Formula: bayesian_score = (v*R + m*C) / (v + m)
    - R = observed frequency (count / total)
    - v = observation count
    - m = prior strength (PRIOR_STRENGTH)
    - C = base rate (average frequency across all tags in this dimension)

    The ``tier`` field classifies entries by raw frequency:
    - "strong": frequency >= STRONG_THRESHOLD (≥50%)
    - "weak":   frequency >= WEAK_THRESHOLD (≥30%)
    - "":       below threshold (filtered out before report)
    """

    name: str
    count: int  # Raw appearances in liked/disliked set
    total: int  # Total liked/disliked titles
    base_rate: float  # Average frequency across all tags in this dimension
    bayesian_score: float = 0.0  # Computed after init
    tier: str = ""  # "strong", "weak", or "" (filtered out)

    @property
    def raw_frequency(self) -> float:
        """Raw observed frequency before Bayesian shrinkage."""
        return self.count / self.total if self.total > 0 else 0.0

    @property
    def percent(self) -> str:
        """Human-readable Bayesian score as percentage."""
        return f"{self.bayesian_score * 100:.1f}%"


@dataclass
class ConfidenceReport:
    """Full confidence report across all dimensions, split by tier."""

    total_liked: int = 0
    total_disliked: int = 0

    # Genres
    strong_genres: list[ConfidenceEntry] = field(default_factory=list)
    weak_genres: list[ConfidenceEntry] = field(default_factory=list)
    disliked_genres: list[ConfidenceEntry] = field(default_factory=list)

    # Studios
    strong_studios: list[ConfidenceEntry] = field(default_factory=list)
    weak_studios: list[ConfidenceEntry] = field(default_factory=list)

    # Directors
    strong_directors: list[ConfidenceEntry] = field(default_factory=list)
    weak_directors: list[ConfidenceEntry] = field(default_factory=list)

    # Writers
    strong_writers: list[ConfidenceEntry] = field(default_factory=list)
    weak_writers: list[ConfidenceEntry] = field(default_factory=list)


def _build_entries(
    counter: Counter[str],
    total: int,
    min_count: int,
) -> list[ConfidenceEntry]:
    """Build sorted entries with Bayesian-averaged scores and tiers.

    Steps:
    1. Compute base rate C = mean frequency across all tags
    2. For each tag: bayesian = (v*R + m*C) / (v + m)
    3. Assign tier based on raw frequency (strong/weak/ignored)
    4. Filter by min_count AND weak threshold, sort by bayesian desc

    Args:
        counter: Feature occurrence counts.
        total: Total number of subjects in this set.
        min_count: Minimum occurrences to be considered a preference.

    Returns:
        Sorted list of ConfidenceEntry (highest bayesian first).
    """
    if not counter or total == 0:
        return []

    all_freqs = [count / total for count in counter.values()]
    base_rate = sum(all_freqs) / len(all_freqs)
    m = PRIOR_STRENGTH

    entries: list[ConfidenceEntry] = []
    for name, count in counter.items():
        if count < min_count:
            continue
        r = count / total
        if r < WEAK_THRESHOLD:
            continue
        bayesian = (count * r + m * base_rate) / (count + m)
        tier = "strong" if r >= STRONG_THRESHOLD else "weak"
        entries.append(
            ConfidenceEntry(
                name=name,
                count=count,
                total=total,
                base_rate=base_rate,
                bayesian_score=bayesian,
                tier=tier,
            )
        )

    entries.sort(key=lambda e: e.bayesian_score, reverse=True)
    return entries


def _split_by_tier(
    entries: list[ConfidenceEntry],
) -> tuple[list[ConfidenceEntry], list[ConfidenceEntry]]:
    """Split entries into strong and weak lists."""
    strong = [e for e in entries if e.tier == "strong"]
    weak = [e for e in entries if e.tier == "weak"]
    return strong, weak


def _resolve_genre_overlap(
    raw_liked: list[ConfidenceEntry],
    raw_disliked: list[ConfidenceEntry],
) -> tuple[list[ConfidenceEntry], list[ConfidenceEntry]]:
    """Resolve genre overlap: keep each genre on the higher-scoring side.

    Args:
        raw_liked: Liked genre entries.
        raw_disliked: Disliked genre entries.

    Returns:
        (final_liked_genres, final_disliked_genres) with overlaps resolved.
    """
    liked_set = {e.name: e for e in raw_liked}
    disliked_set = {e.name: e for e in raw_disliked}
    overlap = set(liked_set) & set(disliked_set)

    final_liked = [
        e
        for e in raw_liked
        if e.name not in overlap
        or e.bayesian_score >= disliked_set[e.name].bayesian_score
    ]
    final_disliked = [
        e
        for e in raw_disliked
        if e.name not in overlap or e.bayesian_score > liked_set[e.name].bayesian_score
    ]
    return final_liked, final_disliked


def _count_features(
    subjects: list[CachedSubject],
) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    """Count feature occurrences across a set of subjects.

    Returns:
        (genres, studios, directors, writers) counters.
    """
    genres: Counter[str] = Counter()
    studios: Counter[str] = Counter()
    directors: Counter[str] = Counter()
    writers: Counter[str] = Counter()

    for cs in subjects:
        feat = extract_features(cs)
        genres.update(feat.genres)
        studios.update(feat.studios)
        directors.update(feat.directors)
        writers.update(feat.writers)

    return genres, studios, directors, writers


def compute_confidence(
    liked_subjects: list[CachedSubject],
    disliked_subjects: list[CachedSubject],
    *,
    min_count: int = MIN_COUNT,
) -> ConfidenceReport:
    """Compute frequency-based confidence scores for all feature dimensions.

    Args:
        liked_subjects: Subjects with rate >= 7.
        disliked_subjects: Subjects with rate < 5 or dropped.
        min_count: Minimum occurrences to be considered a preference.

    Returns:
        A ConfidenceReport with sorted entries for each dimension.
    """
    total_liked = len(liked_subjects)
    total_disliked = len(disliked_subjects)

    liked_genres, liked_studios, liked_directors, liked_writers = _count_features(
        liked_subjects
    )

    disliked_genres = Counter[str]()
    for cs in disliked_subjects:
        feat = extract_features(cs)
        disliked_genres.update(feat.genres)

    # Resolve genre overlap between liked and disliked
    raw_liked = _build_entries(liked_genres, total_liked, min_count)
    raw_disliked = _build_entries(disliked_genres, total_disliked, min_count)
    final_liked_genres, final_disliked_genres = _resolve_genre_overlap(
        raw_liked,
        raw_disliked,
    )
    strong_genres, weak_genres = _split_by_tier(final_liked_genres)

    strong_studios, weak_studios = _split_by_tier(
        _build_entries(liked_studios, total_liked, min_count),
    )
    strong_directors, weak_directors = _split_by_tier(
        _build_entries(liked_directors, total_liked, min_count),
    )
    strong_writers, weak_writers = _split_by_tier(
        _build_entries(liked_writers, total_liked, min_count),
    )

    return ConfidenceReport(
        total_liked=total_liked,
        total_disliked=total_disliked,
        strong_genres=strong_genres,
        weak_genres=weak_genres,
        disliked_genres=final_disliked_genres,
        strong_studios=strong_studios,
        weak_studios=weak_studios,
        strong_directors=strong_directors,
        weak_directors=weak_directors,
        strong_writers=strong_writers,
        weak_writers=weak_writers,
    )


# ------------------------------------------------------------------ #
# Formatting for LLM consumption
# ------------------------------------------------------------------ #


def format_confidence_report(report: ConfidenceReport) -> str:
    """Format a ConfidenceReport as human-readable text for the LLM.

    Output is organized by tier (strong/weak) then by dimension
    (genres/studios/directors/writers), making it clear which
    preferences are high-confidence vs tentative.
    """
    lines: list[str] = [
        "## Tag Confidence Analysis (Tiered, Bayesian Averaged)",
        f"Total: {report.total_liked} liked, {report.total_disliked} disliked",
        f"Thresholds: strong ≥{STRONG_THRESHOLD:.0%}, "
        f"weak ≥{WEAK_THRESHOLD:.0%}, below = ignored\n",
    ]

    def _format_entries(entries: list[ConfidenceEntry]) -> None:
        for e in entries:
            lines.append(
                f"- {e.name}: {e.count}/{e.total} "
                f"(raw {e.raw_frequency * 100:.0f}%, "
                f"bayesian {e.percent})",
            )

    def _format_tier_section(
        title: str,
        genres: list[ConfidenceEntry],
        studios: list[ConfidenceEntry],
        directors: list[ConfidenceEntry],
        writers: list[ConfidenceEntry],
    ) -> None:
        if not any((genres, studios, directors, writers)):
            return
        lines.append(f"### {title}")
        if genres:
            lines.append("#### Genres")
            _format_entries(genres)
        if studios:
            lines.append("#### Studios")
            _format_entries(studios)
        if directors:
            lines.append("#### Directors")
            _format_entries(directors)
        if writers:
            lines.append("#### Writers")
            _format_entries(writers)
        lines.append("")

    _format_tier_section(
        "Strong Preferences (≥50%)",
        report.strong_genres,
        report.strong_studios,
        report.strong_directors,
        report.strong_writers,
    )
    _format_tier_section(
        "Weak Preferences (30-50%)",
        report.weak_genres,
        report.weak_studios,
        report.weak_directors,
        report.weak_writers,
    )

    if report.disliked_genres:
        lines.append("### Disliked Genres")
        _format_entries(report.disliked_genres)
        lines.append("")

    return "\n".join(lines)
