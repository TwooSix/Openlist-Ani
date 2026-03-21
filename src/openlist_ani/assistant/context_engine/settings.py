"""Centralised configuration dataclasses for the context engine.

Modelled after OpenClaw's ``EffectiveContextPruningSettings`` and
compaction configuration, adapted for our single-user Telegram bot.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PruningSettings:
    """Controls how old tool results are trimmed at read-time.

    Attributes:
        keep_last_assistants: Number of recent assistant turns whose tool
            results are protected from hard-clearing.
        soft_trim_max_chars: Tool results exceeding this limit in
            *recent* turns are soft-trimmed (head + tail kept).
        soft_trim_head_chars: Characters to keep from the beginning.
        soft_trim_tail_chars: Characters to keep from the end.
        hard_clear_placeholder: Replacement text for cleared tool results.
    """

    keep_last_assistants: int = 3
    soft_trim_max_chars: int = 4_000
    soft_trim_head_chars: int = 1_500
    soft_trim_tail_chars: int = 1_500
    hard_clear_placeholder: str = "[Old tool result content cleared]"


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    """Controls when and how session history is compressed.

    Attributes:
        session_max_tokens: Estimated token budget for a single session.
        keep_recent_turns: Turns to preserve verbatim after compaction.
        memory_flush_threshold: Fraction of ``session_max_tokens`` at
            which the pre-compaction memory flush is triggered.
    """

    session_max_tokens: int = 100_000
    keep_recent_turns: int = 4
    memory_flush_threshold: float = 0.8


@dataclass(frozen=True, slots=True)
class SkillCatalogSettings:
    """Controls how the skill catalog is injected into the system prompt.

    Attributes:
        max_skills_prompt_chars: Hard cap on the total character length of
            the skills section in the system prompt.  If the full format
            exceeds this budget the catalog degrades to compact mode
            (name + path only, descriptions omitted).
    """

    max_skills_prompt_chars: int = 8_000
