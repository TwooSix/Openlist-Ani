"""Skill Catalog — discovery + compact prompt generation.

Modelled after OpenClaw's ``skills/workspace.ts``:

1. Scan ``skills/*/SKILL.md`` and parse YAML frontmatter.
2. Generate a compact skill list (name + description + path) for the
   system prompt, respecting a character budget.
3. If the full format exceeds the budget, degrade to compact mode
   (name + path only, descriptions omitted).

The LLM reads the full ``SKILL.md`` on-demand via the ``read_file`` tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ...logger import logger
from .settings import SkillCatalogSettings


@dataclass(slots=True)
class SkillEntry:
    """One discovered skill with parsed metadata."""

    name: str
    description: str
    usage: str
    doc_path: str  # absolute POSIX path to SKILL.md


class SkillCatalog:
    """Discover skills and build the system-prompt section.

    Args:
        skills_dir: Root directory containing ``<skill_name>/SKILL.md``.
        settings: Catalog size budget configuration.
    """

    def __init__(
        self,
        skills_dir: Path | None = None,
        settings: SkillCatalogSettings | None = None,
    ) -> None:
        self._skills_dir = skills_dir or (
            Path(__file__).resolve().parent.parent / "skills"
        )
        self._settings = settings or SkillCatalogSettings()
        self._entries: list[SkillEntry] | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load(self) -> list[SkillEntry]:
        """Scan the skills directory and return parsed entries.

        Results are cached after the first call.
        """
        if self._entries is not None:
            return self._entries
        self._entries = self._discover_skills()
        return self._entries

    def build_prompt(self) -> str:
        """Build the skills section for the system prompt.

        Returns the full format if within budget, otherwise degrades to
        compact mode (name + path only).  If compact mode still exceeds
        the budget, skills are dropped from the tail.
        """
        entries = self.load()
        if not entries:
            return self._base_hint("")

        full = self._format_full(entries)
        if len(full) <= self._settings.max_skills_prompt_chars:
            return self._base_hint(full)

        compact = self._format_compact(entries)
        if len(compact) <= self._settings.max_skills_prompt_chars:
            return self._base_hint(compact)

        # Binary-search for the largest prefix that fits in compact mode.
        lo, hi = 0, len(entries)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if (
                len(self._format_compact(entries[:mid]))
                <= self._settings.max_skills_prompt_chars
            ):
                lo = mid
            else:
                hi = mid - 1
        return self._base_hint(self._format_compact(entries[:lo]))

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_skills(self) -> list[SkillEntry]:
        """Walk the skills directory and parse frontmatter."""
        fm_re = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
        entries: list[SkillEntry] = []

        if not self._skills_dir.exists():
            return entries

        for skill_md in self._skills_dir.rglob("SKILL.md"):
            try:
                content = skill_md.read_text(encoding="utf-8")
                match = fm_re.search(content)
                if not match:
                    continue
                yaml_block = match.group(1)
                name = self._extract_yaml_field(yaml_block, "name")
                name = name or skill_md.parent.name
                desc = self._extract_yaml_field(yaml_block, "description")
                desc = desc or "No description."
                usage = self._extract_yaml_field(yaml_block, "usage") or ""
                entries.append(
                    SkillEntry(
                        name=name,
                        description=desc,
                        usage=usage,
                        doc_path=skill_md.as_posix(),
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to parse skill {skill_md}: {e}")

        return entries

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_full(entries: list[SkillEntry]) -> str:
        """Full format: name + description + usage + path."""
        lines: list[str] = []
        for e in entries:
            line = f"- **{e.name}**: {e.description}"
            if e.usage:
                line += f"\n  - *When to use*: {e.usage}"
            line += f"\n  - *Documentation*: `{e.doc_path}`"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _format_compact(entries: list[SkillEntry]) -> str:
        """Compact format: name + path only (no description)."""
        return "\n".join(f"- **{e.name}**: `{e.doc_path}`" for e in entries)

    @staticmethod
    def _base_hint(skill_list: str) -> str:
        """Wrap skill list with discovery instructions."""
        hint = (
            "## Skill Discovery\n\n"
            "You have domain-specific skills available as standalone scripts.\n"
        )
        if skill_list:
            hint += "### Available Skills\n\n" + skill_list + "\n\n"
        hint += (
            "### How to use them\n\n"
            "1. Use `read_file` on the provided *Documentation* path to learn "
            "about available actions and their precise arguments.\n"
            "2. Use `run_skill` to execute the skill, e.g.:\n"
            '   `run_skill(skill_module="bangumi.script.calendar", '
            'arguments={"weekday": 1})`\n\n'
            "Always read SKILL.md first so you know the correct arguments.\n"
        )
        return hint

    # ------------------------------------------------------------------
    # YAML helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_yaml_field(yaml_block: str, field: str) -> str | None:
        """Extract a single scalar YAML field value from *yaml_block*."""
        m = re.search(
            rf"^{re.escape(field)}:\s*[\"']?(.*?)[\"']?$",
            yaml_block,
            re.MULTILINE,
        )
        return m.group(1).strip() if m else None
