"""Collection-release title detection."""

from __future__ import annotations

import re

# Keep patterns anchored enough to avoid treating ordinary single-episode
# titles such as "Show S02 - 14" or "The Bad Batch - 01" as collections.
_COLLECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"合集"),
    re.compile(r"全集"),
    re.compile(r"总集篇"),
    re.compile(r"全套"),
    re.compile(r"全\s*[0-9一二三四五六七八九十百两]+\s*(?:集|话|卷|季)"),
    re.compile(r"(?:百度网盘|网盘)?打包下载"),
    re.compile(r"(?i)\bcomplete\b"),
    re.compile(
        r"(?i)(?:"
        r"[\[(【]\s*(?:official\s+|unofficial\s+|ultimate\s+)?batch\s*[\])】]"
        r"|[-_/|]\s*(?:official\s+|unofficial\s+|ultimate\s+)?batch\b"
        r"|\b(?:official|unofficial|ultimate)\s+batch\b"
        r")"
    ),
    re.compile(r"\bBATCH\b"),
    re.compile(r"(?i)BD[-\s]*BOX"),
    re.compile(r"(?i)\bTV\s*[+＋]\s*(?:OADs?|OVAs?|Movies?|剧场版)\b"),
    re.compile(r"(?i)\bSeasons?\s*\d{1,2}\s*[~–—-]\s*\d{1,2}\b"),
    re.compile(r"(?i)\bS\d{1,2}E\d{1,3}\s*[~–—-]\s*E?\d{1,3}\b"),
    re.compile(r"(?i)\bS(?:eason)?\s*\d{1,2}\s*Complete\b"),
    re.compile(r"(?i)\b(?:Ep(?:isodes?)?|Eps?)\s*0?\d{1,3}\s*[~–—-]\s*\d{1,3}\b"),
    re.compile(r"(?<![\dA-Za-z])\d{2,3}\s*[~–—-]\s*\d{2,3}(?!\d)"),
    re.compile(
        r"(?i)(?<![\dA-Za-z])\d{1,3}\s*[~–—-]\s*\d{1,3}\s*\+\s*(?:OVA|OAD|SP|Movies?|剧场版)"
    ),
    re.compile(r"(?i)(?<![\dA-Za-z])\d{1,3}\s*[~–—-]\s*\d{1,3}(?=\s*[\[(（])"),
    re.compile(r"(?i)(?<![\dA-Za-z])\d{1,3}\s*[~–—-]\s*\d{1,3}\s*(?:end|fin|完)"),
)


def detect_collection(title: str) -> tuple[bool, str | None]:
    """Return ``(is_collection, matched_fragment)`` for ``title``."""
    if not title:
        return False, None
    for pattern in _COLLECTION_PATTERNS:
        match = pattern.search(title)
        if match:
            return True, match.group(0)
    return False, None
