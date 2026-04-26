"""Pre-compute Bayesian weighted scores for candidate anime.

Parses the text output of ``bangumi/calendar`` to extract subject IDs,
names, scores, vote counts, and ranks — then computes Bayesian weighted
averages.  **Zero additional API calls.**

    weighted = (v / (v + m)) * R + (m / (v + m)) * C
"""

from __future__ import annotations

import re

from loguru import logger

# Prior weight: how many votes a title needs before we trust its score.
# With m=50, a title with 6 votes is pulled ~89% toward the global mean.
PRIOR_WEIGHT = 50


def _parse_calendar_items(raw: str) -> list[dict]:
    """Extract candidate info from ``bangumi/calendar`` text output.

    Expected line format (from ``calendar.py._format_item``):
        ``  - [ID:12345] Title (Name) score:7.5 votes:978 rank:#123``

    Fields ``score``, ``votes``, ``rank`` are optional.

    Args:
        raw: Full text output from ``bangumi/calendar``.

    Returns:
        List of dicts with keys: id, name, score, votes, rank.
    """
    pattern = re.compile(
        r"\[ID:(\d+)\]\s+"
        r"(.+?)"
        r"(?:\s+score:([\d.]+))?"
        r"(?:\s+votes:(\d+))?"
        r"(?:\s+rank:#(\d+))?"
        r"\s*$",
        re.MULTILINE,
    )

    items: list[dict] = []
    seen: set[int] = set()
    for m in pattern.finditer(raw):
        sid = int(m.group(1))
        if sid in seen:
            continue
        seen.add(sid)
        items.append(
            {
                "id": sid,
                "name": m.group(2).strip(),
                "score": float(m.group(3)) if m.group(3) else 0.0,
                "votes": int(m.group(4)) if m.group(4) else 0,
                "rank": int(m.group(5)) if m.group(5) else 0,
            }
        )
    return items


def _compute_bayesian_scores(candidates: list[dict]) -> list[dict]:
    """Compute Bayesian weighted scores and sort candidates.

    Args:
        candidates: List of dicts from ``_parse_calendar_items``.

    Returns:
        Sorted list (highest weighted first) with ``weighted`` key.
    """
    scored = [c for c in candidates if c["score"] > 0]
    if not scored:
        return candidates

    global_avg = sum(c["score"] for c in scored) / len(scored)
    m = PRIOR_WEIGHT

    for c in candidates:
        v = c["votes"]
        r = c["score"]
        if r > 0 and v > 0:
            c["weighted"] = (v / (v + m)) * r + (m / (v + m)) * global_avg
        else:
            c["weighted"] = 0.0
        c["global_avg"] = global_avg

    candidates.sort(key=lambda c: c["weighted"], reverse=True)
    return candidates


def _format_results(candidates: list[dict]) -> str:
    """Format scored candidates for the LLM."""
    if not candidates:
        return "No candidates to score."

    global_avg = candidates[0].get("global_avg", 0.0)
    lines: list[str] = [
        "## Candidates (Bayesian Weighted Score)",
        f"Global avg: {global_avg:.2f} | Prior weight m={PRIOR_WEIGHT}\n",
    ]

    for i, c in enumerate(candidates, 1):
        raw = c["score"]
        if raw <= 0:
            continue
        votes = c["votes"]
        weighted = c.get("weighted", 0.0)
        line = (
            f"{i}. [ID:{c['id']}] {c['name']} — "
            f"raw: {raw:.1f}, votes: {votes}, "
            f"weighted: {weighted:.2f}"
        )
        if votes < PRIOR_WEIGHT:
            line += "  (few votes, pulled toward avg)"
        lines.append(line)

    lines.append("\n---\nRank by weighted score for selection; show raw score to user.")
    return "\n".join(lines)


def run(subjects: str = "", **kwargs) -> str:
    """Score candidate anime from calendar data. Zero API calls.

    Pass the full text output of ``bangumi/calendar`` as ``subjects``.

    Args:
        subjects: Full text output from ``bangumi/calendar``.
    """
    if not subjects:
        return (
            "Error: 'subjects' parameter is required.\n"
            "Pass the full output of bangumi/calendar."
        )

    candidates = _parse_calendar_items(subjects)
    if not candidates:
        return "Error: No valid items found in calendar output."

    logger.info(f"Scoring {len(candidates)} candidates from calendar data")
    scored = _compute_bayesian_scores(candidates)
    return _format_results(scored)
