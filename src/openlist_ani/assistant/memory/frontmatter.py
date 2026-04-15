"""
YAML frontmatter parser for memory files.

Extracts and formats YAML frontmatter blocks delimited by ``---``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Matches a YAML frontmatter block at the start of a string.
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n((?:[^\n]*\n)*?)---[ \t]*\n",
)

# Valid memory types (closed taxonomy).
VALID_MEMORY_TYPES = frozenset({"user", "project", "feedback", "reference"})


@dataclass
class Frontmatter:
    """Parsed YAML frontmatter from a memory file."""

    name: str = ""
    type: str = ""
    description: str = ""
    raw: dict[str, str] = field(default_factory=dict)


def parse_frontmatter(text: str) -> tuple[Frontmatter, str]:
    """Parse YAML frontmatter from a memory file.

    Args:
        text: Full file content.

    Returns:
        (frontmatter, body) where body is the content after the frontmatter.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return Frontmatter(), text

    yaml_block = match.group(1)
    body = text[match.end():]

    raw: dict[str, str] = {}
    for line in yaml_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            raw[key.strip()] = value.strip().strip("\"'")

    fm = Frontmatter(
        name=raw.get("name", ""),
        type=raw.get("type", ""),
        description=raw.get("description", ""),
        raw=raw,
    )
    return fm, body


def format_frontmatter(data: dict[str, str]) -> str:
    """Format a dict as YAML frontmatter block.

    Args:
        data: Key-value pairs for the frontmatter.

    Returns:
        String like ``---\\nkey: value\\n---\\n``.
    """
    lines = ["---"]
    for key, value in data.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")  # trailing newline
    return "\n".join(lines)


def parse_memory_type(raw_type: str | None) -> str | None:
    """Validate and normalize a memory type string.

    Returns the type if valid, None otherwise.
    """
    if raw_type is None:
        return None
    normalized = raw_type.strip().lower()
    return normalized if normalized in VALID_MEMORY_TYPES else None
