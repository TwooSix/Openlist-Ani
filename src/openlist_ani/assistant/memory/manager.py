"""
Memory manager -- four-file persistent memory + CLAUDE.md project instructions.

Memory layout under data_dir:
    SOUL.md             -- Agent identity & behavioral rules (system prompt)
    MEMORY.md           -- Persistent long-term facts across sessions
    USER.md             -- User profile / persona built up over time
    sessions/           -- Daily conversation session logs
        SESSION_YYYYMMDD.md

Additionally, project-level CLAUDE.md files are loaded from the project
root for codebase-specific instructions.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

from loguru import logger

# Max lines for MEMORY.md to prevent context bloat
MAX_MEMORY_LINES = 200

# File name constants (avoid duplication)
_MEMORY_FILENAME = "MEMORY.md"
_USER_FILENAME = "USER.md"

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

To invoke a skill, call `skill_tool` with these parameters:
 - `skill_name`: the skill name (see <available_skills> for the full list)
 - `action`: the specific action within the skill
 - `params`: a dict of parameters for the action

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


def _sanitize_path(path: str) -> str:
    """Sanitize a path for use as a directory name."""
    sanitized = re.sub(r'[/\\:*?"<>|]', "_", path)
    sanitized = sanitized.strip("_").strip()
    return sanitized or "default"


class MemoryManager:
    """Manages four-file persistent memory + CLAUDE.md project instructions.

    Four persistent files under data_dir:
        SOUL.md    -- Agent identity, personality, behavioral rules (system prompt)
        MEMORY.md  -- Long-term facts the agent remembers across sessions
        USER.md    -- User profile / persona built up over time
        sessions/SESSION_YYYYMMDD.md -- Daily conversation session logs

    Plus project-level CLAUDE.md files for codebase-specific instructions.
    """

    def __init__(
        self,
        data_dir: Path,
        project_root: Path | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._sessions_dir = data_dir / "sessions"
        self._project_root = project_root or Path.cwd()
        self._ensure_dirs()
        self._ensure_soul()

    # ------------------------------------------------------------------ #
    # Initialization
    # ------------------------------------------------------------------ #

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

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

    # ------------------------------------------------------------------ #
    # SOUL.md -- Agent identity (system prompt)
    # ------------------------------------------------------------------ #

    def load_soul(self) -> str:
        """Load the agent's identity / system prompt from SOUL.md."""
        return self._read_file(self._data_dir / "SOUL.md")

    # ------------------------------------------------------------------ #
    # MEMORY.md -- Persistent long-term facts
    # ------------------------------------------------------------------ #

    def load_memory(self) -> str:
        """Load persistent long-term memory from MEMORY.md."""
        content = self._read_file(self._data_dir / _MEMORY_FILENAME)
        if not content.strip():
            return ""

        # Truncate if too long
        lines = content.strip().split("\n")
        if len(lines) > MAX_MEMORY_LINES:
            content = "\n".join(lines[:MAX_MEMORY_LINES])
            content += (
                f"\n\n> WARNING: MEMORY.md has {len(lines)} lines "
                f"(limit: {MAX_MEMORY_LINES}). Only part was loaded. "
                "Consider compacting old entries."
            )
        return content

    async def append_memory(self, fact: str) -> None:
        """Append a fact to MEMORY.md."""
        memory_file = self._data_dir / _MEMORY_FILENAME
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- [{timestamp}] {fact}\n"
        try:
            await asyncio.to_thread(self._append_text, memory_file, entry)
        except OSError as e:
            logger.error(f"Failed to append memory: {e}")

    # ------------------------------------------------------------------ #
    # USER.md -- User profile / persona
    # ------------------------------------------------------------------ #

    def load_user(self) -> str:
        """Load the user profile from USER.md."""
        return self._read_file(self._data_dir / _USER_FILENAME)

    async def append_user_fact(self, fact: str) -> None:
        """Append a fact to USER.md."""
        user_file = self._data_dir / _USER_FILENAME
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- [{timestamp}] {fact}\n"
        try:
            await asyncio.to_thread(self._append_text, user_file, entry)
        except OSError as e:
            logger.error(f"Failed to append user fact: {e}")

    # ------------------------------------------------------------------ #
    # SESSION -- Daily conversation session logs
    # ------------------------------------------------------------------ #

    async def load_session_history(self) -> str:
        """Load the current day's session history."""
        session_file = self._current_session_file()
        return await asyncio.to_thread(self._read_file, session_file)

    async def append_turn(
        self,
        user_msg: str,
        assistant_msg: str,
        tool_context: str = "",
    ) -> None:
        """Append a conversation turn to the current session file.

        This ensures the assistant remembers what happened even after
        the program restarts.
        """
        session_file = self._current_session_file()
        timestamp = datetime.now().strftime("%H:%M:%S")

        lines = [f"\n### [{timestamp}]"]
        lines.append(f"**User**: {user_msg}")
        if tool_context:
            lines.append(f"**Tools**: {tool_context}")
        lines.append(f"**Assistant**: {assistant_msg}")
        lines.append("")

        entry = "\n".join(lines)
        try:
            await asyncio.to_thread(self._append_text, session_file, entry)
        except OSError as e:
            logger.error(f"Failed to append session turn: {e}")

    async def start_new_session(self) -> None:
        """Start a new session (creates today's session file header if needed)."""
        session_file = self._current_session_file()
        if not session_file.exists():
            date_str = datetime.now().strftime("%Y-%m-%d")
            header = f"# Session {date_str}\n\n"
            try:
                await asyncio.to_thread(
                    session_file.write_text, header, encoding="utf-8"
                )
            except OSError as e:
                logger.error(f"Failed to create session file: {e}")

    def _current_session_file(self) -> Path:
        """Get the path to today's session file."""
        date_str = datetime.now().strftime("%Y%m%d")
        return self._sessions_dir / f"SESSION_{date_str}.md"

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
    # Clear / reset
    # ------------------------------------------------------------------ #

    async def clear_session(self) -> None:
        """Clear all session history files (keeps SOUL/MEMORY/USER)."""
        for session_file in self._sessions_dir.glob("SESSION_*.md"):
            try:
                await asyncio.to_thread(session_file.unlink)
            except OSError as e:
                logger.error(f"Failed to delete session file {session_file}: {e}")

    async def clear_all(self) -> None:
        """Clear sessions + MEMORY.md + USER.md (keeps SOUL.md)."""
        await self.clear_session()
        # Clear MEMORY.md
        memory_file = self._data_dir / _MEMORY_FILENAME
        try:
            await asyncio.to_thread(
                memory_file.write_text, "", encoding="utf-8"
            )
        except OSError as e:
            logger.error(f"Failed to clear {_MEMORY_FILENAME}: {e}")
        # Clear USER.md
        user_file = self._data_dir / _USER_FILENAME
        try:
            await asyncio.to_thread(
                user_file.write_text, "", encoding="utf-8"
            )
        except OSError as e:
            logger.error(f"Failed to clear {_USER_FILENAME}: {e}")

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (1 token ~ 4 chars)."""
        return len(text) // 4

    @staticmethod
    def _append_text(path: Path, text: str) -> None:
        """Synchronous helper to append text to a file (used via asyncio.to_thread)."""
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)

    def _read_file(self, path: Path) -> str:
        """Read a text file, returning empty string if not found."""
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read {path}: {e}")
            return ""
