"""Session Pruning — read-time tool-result trimming.

Ported from OpenClaw's ``context-pruning/pruner.ts``.

Key design principle: **disk state is never modified**.  All pruning
happens in-memory when reconstructing the LLM context, so the session
Markdown files remain complete and human-readable.

Two pruning tiers:
1. **Hard clear** — older turns (outside ``keep_last_assistants``) have
   their tool results replaced with a short placeholder.
2. **Soft trim** — recent turns keep the head and tail of large tool
   results, with a note about the removed middle section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .settings import PruningSettings


@dataclass(slots=True)
class ParsedTurn:
    """One conversation turn parsed from a session file."""

    user_text: str
    tool_context: str
    assistant_text: str


def soft_trim_text(text: str, settings: PruningSettings) -> str:
    """Trim a single tool-result text block, keeping head and tail.

    If *text* is within the ``soft_trim_max_chars`` budget, return it
    unchanged.  Otherwise return the first ``soft_trim_head_chars`` and
    last ``soft_trim_tail_chars`` characters joined by a marker.

    Args:
        text: Raw tool-result text.
        settings: Pruning configuration.

    Returns:
        The (possibly truncated) text.
    """
    if len(text) <= settings.soft_trim_max_chars:
        return text

    head = text[: settings.soft_trim_head_chars]
    tail = text[-settings.soft_trim_tail_chars :]
    removed = len(text) - settings.soft_trim_head_chars - settings.soft_trim_tail_chars
    return f"{head}\n    ... [Tool result trimmed: removed {removed} chars] ...\n{tail}"


def hard_clear_tool_context(tool_ctx: str, settings: PruningSettings) -> str:
    """Replace every ``Result:`` body in *tool_ctx* with a placeholder.

    Args:
        tool_ctx: Multi-line tool context block from session.
        settings: Pruning configuration.

    Returns:
        Tool context with result bodies replaced.
    """
    return re.sub(
        r"( {2}Result:).*(?=\n {2}Called |\n {2}Result:|\Z)",
        rf"\1 {settings.hard_clear_placeholder}",
        tool_ctx,
        flags=re.DOTALL,
    )


def soft_trim_tool_context(tool_ctx: str, settings: PruningSettings) -> str:
    """Soft-trim oversized ``Result:`` bodies in *tool_ctx*.

    Args:
        tool_ctx: Multi-line tool context block from session.
        settings: Pruning configuration.

    Returns:
        Tool context with large result bodies trimmed.
    """

    def _replace(m: re.Match) -> str:
        prefix = m.group(1)  # "  Result:"
        body = m.group(2)
        if len(body) <= settings.soft_trim_max_chars:
            return m.group(0)
        return f"{prefix}\n{soft_trim_text(body, settings)}"

    return re.sub(
        r"( {2}Result:)(.*)(?=\n {2}Called |\n {2}Result:|\Z)",
        _replace,
        tool_ctx,
        flags=re.DOTALL,
    )


def prune_turn_messages(
    turns: list[ParsedTurn],
    settings: PruningSettings,
) -> list[dict[str, str]]:
    """Apply session pruning to parsed turns and return LLM message dicts.

    This is the main entry point.  It implements the two-tier pruning
    strategy:

    * Turns outside the ``keep_last_assistants`` window → hard clear.
    * Recent turns → soft trim oversized results.

    Args:
        turns: Ordered list of parsed conversation turns.
        settings: Pruning configuration.

    Returns:
        Flat list of ``{role, content}`` dicts ready for the LLM.
    """
    total = len(turns)
    messages: list[dict[str, str]] = []

    for i, turn in enumerate(turns):
        is_recent = (total - i) <= settings.keep_last_assistants

        if turn.user_text:
            messages.append({"role": "user", "content": turn.user_text})

        if turn.tool_context:
            if is_recent:
                pruned_ctx = soft_trim_tool_context(turn.tool_context, settings)
            else:
                pruned_ctx = hard_clear_tool_context(turn.tool_context, settings)

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "[Previous tool executions for this turn]\n" + pruned_ctx
                    ),
                }
            )

        if turn.assistant_text:
            messages.append({"role": "assistant", "content": turn.assistant_text})

    return messages
