"""
Consolidation prompt builder for auto-dream.

Builds the 4-phase dream prompt: orient -> gather -> consolidate -> prune.
"""

from __future__ import annotations


def build_consolidation_prompt(
    memory_dir: str,
    sessions_dir: str,
    session_ids: list[str],
) -> str:
    """Build the multi-phase consolidation prompt for the dream agent.

    Args:
        memory_dir: Absolute path to the ``memory/`` directory.
        sessions_dir: Absolute path to the ``sessions/`` directory.
        session_ids: List of session IDs to review.

    Returns:
        The full prompt string.
    """
    session_list = "\n".join(f"- {sid}" for sid in session_ids)
    n = len(session_ids)

    return f"""\
# Dream: Memory Consolidation

Memory directory: `{memory_dir}`
Session transcripts: `{sessions_dir}` (JSONL files — grep narrowly, don't read whole files)

## Phase 1 — Orient
- ls the memory directory to see what exists
- Read MEMORY.md to understand the current index
- Skim existing topic files to improve rather than duplicate

## Phase 2 — Gather recent signal
Look for new information worth persisting:
1. Existing memories that drifted — facts that contradict current state
2. Session transcript search — grep JSONL transcripts for narrow terms like user preferences, corrections, project decisions

Don't exhaustively read transcripts. Look only for things you suspect matter.

## Phase 3 — Consolidate
For each thing worth remembering:
- Write or update a memory file with proper YAML frontmatter:
  ```
  ---
  name: Topic Name
  type: user|project|feedback|reference
  description: One-line description
  ---
  ```
- Merge into existing topic files rather than creating duplicates
- Convert relative dates ("yesterday", "last week") to absolute dates
- Delete contradicted facts at the source

## Phase 4 — Prune and index
Update MEMORY.md index:
- Keep under 200 lines and ~25KB
- Each entry: one line, under ~150 chars: `- [Title](file.md) — one-line hook`
- Remove stale/superseded pointers
- Add pointers to newly important memories

Sessions since last consolidation ({n}):
{session_list}
"""
