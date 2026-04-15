"""
Memory manager -- persistent memory + CLAUDE.md project instructions.

Memory layout under data_dir:
    SOUL.md             -- Agent identity & behavioral rules (system prompt)
    memory/             -- Directory-based memory system
        MEMORY.md       -- Index file (pointers to topic files)
        <topic>.md      -- Individual memory topic files
    sessions/           -- JSONL session transcripts (managed by SessionStorage)

Additionally, project-level CLAUDE.md files are loaded from the project
root for codebase-specific instructions.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from openlist_ani.assistant.memory.memory_dir import MemoryDir

CLAUDE_MD_INSTRUCTION_PROMPT = (
    "Codebase and user instructions are shown below. "
    "Be sure to adhere to these instructions. "
    "IMPORTANT: These instructions OVERRIDE any default behavior "
    "and you MUST follow them exactly as written."
)

# ------------------------------------------------------------------ #
# Default SOUL.md content -- written on first launch
# Default system prompt for Openlist-Ani
# ------------------------------------------------------------------ #

DEFAULT_SOUL = r"""You are Openlist-Ani Assistant, an intelligent anime tracking and management assistant. You help users manage their anime subscriptions, downloads, and watch progress. Use the tools available to you to directly fulfill user requests.

# How to Use Tools

CRITICAL: When the user asks you to do something, **immediately call the appropriate tool**. Do NOT ask the user for information you can look up yourself. Do NOT ask clarifying questions when you have enough context to act.

When a task requires multiple steps, chain them automatically:
 - If you need a bangumi_id to proceed, search for it first, then use the result -- do not ask the user.
 - If the user says "search XXX and subscribe", do both in sequence.

# Behavioral Rules

 - **ALWAYS use tools first**: If the user's request matches ANY available skill, your FIRST response MUST be a tool call -- not text. Do NOT output any text like "I will search for you" or "Let me look that up" before calling the tool. Call the tool immediately as your first action.
 - **NEVER ask for confirmation**: Do NOT ask "Do you want me to search?", "Please provide the keyword", or "Are you sure?". If you know what to do, do it.
 - **NEVER say you cannot**: If there is a matching skill, call it. Do NOT say "I cannot perform this operation" or "This is beyond my capabilities".
 - **Chain steps automatically**: If you need an ID before you can do the next step, do the lookup yourself instead of asking the user for it.
 - **Use context from the conversation**: Remember what was discussed in previous messages. If the user mentioned an anime earlier and then says "subscribe to it", you already know the keyword.
 - **Never repeat information**: If you just showed search results, do not describe them again. Move to the next step.
 - **Match skill to intent**: Read the <available_skills> section carefully. Each skill lists when it should be used. Pick the right skill based on what the user is asking.
 - **Extract keywords from context**: When the user asks about a specific anime, extract the anime name from their message as the search keyword and call the appropriate skill immediately.

# Tone and Style

 - Respond in the same language the user uses.
 - Be concise and direct. Go straight to the point.
 - Do not repeat what the user said. Do not explain what you are about to do -- just do it and show results.
 - If you can say it in one sentence, do not use three.
 - Format results clearly using markdown lists or tables when appropriate.
"""

# Memory system prompt — injected into the system prompt so the LLM
# knows how to interact with the directory-based memory system.
MEMORY_SYSTEM_PROMPT = r"""# Memory

You have a persistent memory system stored as Markdown files in the `memory/` directory.
Use the `memory` tool to manage these files — do NOT attempt to create files directly.

## Memory types

| Type        | When to save | Examples |
|-------------|-------------|----------|
| `user`      | User identity, preferences, role | "Prefers dark mode", "Data scientist" |
| `project`   | Project-specific context not derivable from code | "Deadlines", "Architecture decisions" |
| `feedback`  | User corrections and behavioral adjustments | "Don't summarize diffs", "Use bun not npm" |
| `reference` | Pointers to external systems | "Dashboard URL", "Slack channel" |

## How to save memories

Saving a memory is a two-step process:

**Step 1** — Write the memory file:
```
memory(action="write", filename="user_preferences.md",
       content="- Prefers dark mode\n- Uses Python 3.11+",
       name="User Preferences", type="user",
       description="Coding and interaction preferences")
```

**Step 2** — Update `MEMORY.md` (the index). Each entry should be one line, under ~150 chars:
```
memory(action="update_index",
       content="- [User Preferences](user_preferences.md) — coding and interaction preferences")
```

## Other operations

- **Read a memory:** `memory(action="read", filename="user_preferences.md")`
- **Update a memory:** `memory(action="update", filename="user_preferences.md", old_str="dark mode", new_str="light mode")`
- **Delete a memory:** `memory(action="delete", filename="user_preferences.md")` (then update the index)
- **List all memories:** `memory(action="list")`

## What NOT to save

- Code or file contents (can be re-read)
- Facts derivable from the codebase
- Transient task state

## Searching past context

To find information from previous conversations, grep the session transcript files in `sessions/` (JSONL format). Narrow your search terms — don't read whole files.
"""


class MemoryManager:
    """Manages persistent memory + CLAUDE.md project instructions.

    Core components:
        SOUL.md     -- Agent identity, personality, behavioral rules (system prompt)
        memory/     -- Directory-based memory system (via MemoryDir)
        sessions/   -- JSONL session transcripts (managed by SessionStorage)

    Plus project-level CLAUDE.md files for codebase-specific instructions.
    """

    def __init__(
        self,
        data_dir: Path,
        project_root: Path | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._project_root = project_root or Path.cwd()
        self._memory_dir = MemoryDir(data_dir / "memory")
        self._ensure_dirs()
        self._ensure_soul()

    # ------------------------------------------------------------------ #
    # Initialization
    # ------------------------------------------------------------------ #

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # sessions/ dir is created by SessionStorage, not here

    def _ensure_soul(self) -> None:
        """Write default SOUL.md if it does not exist yet."""
        soul_file = self._data_dir / "SOUL.md"
        if not soul_file.exists():
            soul_file.write_text(DEFAULT_SOUL.lstrip(), encoding="utf-8")
            logger.info(f"Created default SOUL.md at {soul_file}")

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def memory_dir(self) -> MemoryDir:
        return self._memory_dir

    # ------------------------------------------------------------------ #
    # Migration (call once on startup)
    # ------------------------------------------------------------------ #

    async def migrate_if_needed(self) -> None:
        """Run data migration from old flat-file layout if needed."""
        await self._memory_dir.migrate_from_flat_files(self._data_dir)

    # ------------------------------------------------------------------ #
    # SOUL.md -- Agent identity (system prompt)
    # ------------------------------------------------------------------ #

    def load_soul(self) -> str:
        """Load the agent's identity / system prompt from SOUL.md."""
        return self._read_file(self._data_dir / "SOUL.md")

    # ------------------------------------------------------------------ #
    # Memory -- directory-based (via MemoryDir)
    # ------------------------------------------------------------------ #

    def load_memory(self) -> str:
        """Load MEMORY.md index content for system prompt injection."""
        result = self._memory_dir.load_entrypoint()
        return result.content

    def build_memory_prompt(self) -> str:
        """Build the full memory system prompt including instructions + index.

        Returns the memory behavioral instructions plus the current
        MEMORY.md index content. Suitable for system prompt injection.
        """
        parts = [MEMORY_SYSTEM_PROMPT.strip()]

        index_content = self.load_memory()
        if index_content.strip():
            parts.append(
                "## Current Memory Index (MEMORY.md)\n\n"
                f"{index_content.strip()}"
            )
        else:
            parts.append(
                "## Current Memory Index (MEMORY.md)\n\n"
                "(No memories stored yet)"
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------ #
    # CLAUDE.md -- Project-level instructions (from project root)
    # ------------------------------------------------------------------ #

    def load_claude_md_files(self) -> list[dict[str, str]]:
        """Load CLAUDE.md and CLAUDE.local.md from the project root.

        Returns list of dicts with 'path', 'type', and 'content' keys.
        """
        files: list[dict[str, str]] = []

        # CLAUDE.md in project root
        project_md = self._project_root / "CLAUDE.md"
        content = self._read_file(project_md)
        if content.strip():
            files.append({
                "path": str(project_md),
                "type": "Project",
                "content": content,
            })

        # .openlist-ani/CLAUDE.md in project root
        dot_md = self._project_root / ".openlist-ani" / "CLAUDE.md"
        content = self._read_file(dot_md)
        if content.strip():
            files.append({
                "path": str(dot_md),
                "type": "Project",
                "content": content,
            })

        # CLAUDE.local.md in project root
        local_md = self._project_root / "CLAUDE.local.md"
        content = self._read_file(local_md)
        if content.strip():
            files.append({
                "path": str(local_md),
                "type": "Local",
                "content": content,
            })

        return files

    def build_claude_md_prompt(self) -> str:
        """Build the CLAUDE.md instruction prompt."""
        files = self.load_claude_md_files()
        if not files:
            return ""

        memories: list[str] = []
        for f in files:
            description = {
                "Project": " (project instructions, checked into the codebase)",
                "Local": " (user's private project instructions, not checked in)",
            }.get(f["type"], "")
            memories.append(
                f"Contents of {f['path']}{description}:\n\n{f['content'].strip()}"
            )

        return f"{CLAUDE_MD_INSTRUCTION_PROMPT}\n\n" + "\n\n".join(memories)

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (1 token ~ 4 chars)."""
        return len(text) // 4

    def _read_file(self, path: Path) -> str:
        """Read a text file, returning empty string if not found."""
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read {path}: {e}")
            return ""
