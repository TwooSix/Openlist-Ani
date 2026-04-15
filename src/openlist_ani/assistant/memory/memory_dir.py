"""
Directory-based memory system aligned with claude-code.

Manages individual memory topic files in a ``memory/`` directory,
with ``MEMORY.md`` as a pure index (pointer file).

Each topic file uses YAML frontmatter for metadata (name, type, description).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from openlist_ani.assistant._constants import (
    MAX_ENTRYPOINT_BYTES,
    MAX_ENTRYPOINT_LINES,
    MAX_MEMORY_FILES,
)
from openlist_ani.assistant.memory.frontmatter import (
    format_frontmatter,
    parse_frontmatter,
    parse_memory_type,
)

ENTRYPOINT_NAME = "MEMORY.md"


@dataclass
class MemoryHeader:
    """Parsed header info from a memory topic file."""

    filename: str  # Relative path within memory dir (e.g. "user_preferences.md")
    file_path: str  # Absolute path
    mtime_ms: float  # Last-modified timestamp (milliseconds since epoch)
    description: str | None  # From frontmatter
    type: str | None  # user / project / feedback / reference


@dataclass
class EntrypointResult:
    """Result of loading the MEMORY.md index file."""

    content: str
    line_count: int
    byte_count: int
    was_line_truncated: bool
    was_byte_truncated: bool


class MemoryDir:
    """Manages the directory-based memory system.

    Storage layout::

        memory/
        +-- MEMORY.md              # Index file (<200 lines, <25KB)
        +-- user_preferences.md    # Topic file (with frontmatter)
        +-- project_context.md
        +-- ...
    """

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._dir

    # ------------------------------------------------------------------ #
    # Scanning
    # ------------------------------------------------------------------ #

    async def scan_memory_files(self) -> list[MemoryHeader]:
        """Scan all ``.md`` files in the memory dir, parse frontmatter.

        Returns headers sorted by mtime (most recent first).
        Excludes the entrypoint (MEMORY.md) itself.
        """
        return await asyncio.to_thread(self._scan_sync)

    def _scan_sync(self) -> list[MemoryHeader]:
        headers: list[MemoryHeader] = []
        if not self._dir.is_dir():
            return headers

        for entry in sorted(self._dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".md":
                continue
            if entry.name == ENTRYPOINT_NAME:
                continue

            try:
                stat = entry.stat()
                text = entry.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning(f"Skipping unreadable memory file {entry}: {e}")
                continue

            fm, _ = parse_frontmatter(text)
            headers.append(
                MemoryHeader(
                    filename=entry.name,
                    file_path=str(entry),
                    mtime_ms=stat.st_mtime * 1000,
                    description=fm.description or None,
                    type=parse_memory_type(fm.type),
                )
            )

        # Sort by mtime descending (most recent first)
        headers.sort(key=lambda h: h.mtime_ms, reverse=True)

        if len(headers) > MAX_MEMORY_FILES:
            logger.warning(
                f"Memory directory has {len(headers)} files "
                f"(limit: {MAX_MEMORY_FILES}). Oldest files may be ignored."
            )
            headers = headers[:MAX_MEMORY_FILES]

        return headers

    def format_memory_manifest(self, headers: list[MemoryHeader]) -> str:
        """Format headers as a text manifest for inclusion in prompts.

        Example output::

            Memory files (3):
            - user_preferences.md [user] — coding and interaction preferences
            - project_context.md [project] — Openlist-Ani architecture
            - feedback_testing.md [feedback] — user corrections on tests
        """
        if not headers:
            return "Memory files: (none)"

        lines = [f"Memory files ({len(headers)}):"]
        for h in headers:
            type_tag = f" [{h.type}]" if h.type else ""
            desc = f" \u2014 {h.description}" if h.description else ""
            lines.append(f"- {h.filename}{type_tag}{desc}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # CRUD operations
    # ------------------------------------------------------------------ #

    def read_memory(self, filename: str) -> str:
        """Read a single memory file's full content.

        Returns empty string if the file doesn't exist.
        """
        path = self._resolve(filename)
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read memory file {filename}: {e}")
            return ""

    async def write_memory(
        self,
        filename: str,
        content: str,
        frontmatter: dict[str, str] | None = None,
    ) -> None:
        """Write or update a memory file.

        If *frontmatter* is provided, it is prepended as a YAML frontmatter
        block. Otherwise the content is written as-is.
        """
        path = self._resolve(filename)

        if frontmatter:
            fm_block = format_frontmatter(frontmatter)
            full_content = fm_block + content
        else:
            full_content = content

        try:
            await asyncio.to_thread(path.write_text, full_content, "utf-8")
            logger.debug(f"Wrote memory file: {filename}")
        except OSError as e:
            logger.error(f"Failed to write memory file {filename}: {e}")

    async def delete_memory(self, filename: str) -> None:
        """Delete a memory file.

        Does nothing if the file doesn't exist. Refuses to delete the
        entrypoint (MEMORY.md).
        """
        if filename == ENTRYPOINT_NAME:
            logger.warning("Cannot delete the entrypoint (MEMORY.md)")
            return

        path = self._resolve(filename)
        if not path.is_file():
            return

        try:
            await asyncio.to_thread(path.unlink)
            logger.info(f"Deleted memory file: {filename}")
        except OSError as e:
            logger.error(f"Failed to delete memory file {filename}: {e}")

    # ------------------------------------------------------------------ #
    # Index (MEMORY.md entrypoint)
    # ------------------------------------------------------------------ #

    def load_entrypoint(self) -> EntrypointResult:
        """Load the MEMORY.md index with truncation enforcement.

        Truncates to ``MAX_ENTRYPOINT_LINES`` and ``MAX_ENTRYPOINT_BYTES``.
        """
        path = self._dir / ENTRYPOINT_NAME
        if not path.is_file():
            return EntrypointResult(
                content="",
                line_count=0,
                byte_count=0,
                was_line_truncated=False,
                was_byte_truncated=False,
            )

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read {ENTRYPOINT_NAME}: {e}")
            return EntrypointResult("", 0, 0, False, False)

        lines = raw.split("\n")
        original_line_count = len(lines)
        original_byte_count = len(raw.encode("utf-8"))

        was_line_truncated = False
        was_byte_truncated = False

        # Line-based truncation
        if len(lines) > MAX_ENTRYPOINT_LINES:
            lines = lines[:MAX_ENTRYPOINT_LINES]
            was_line_truncated = True

        content = "\n".join(lines)

        # Byte-based truncation
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_ENTRYPOINT_BYTES:
            content = content_bytes[:MAX_ENTRYPOINT_BYTES].decode(
                "utf-8", errors="ignore"
            )
            was_byte_truncated = True

        # Append truncation warning if needed
        if was_line_truncated or was_byte_truncated:
            reasons = []
            if was_line_truncated:
                reasons.append(
                    f"{original_line_count} lines > {MAX_ENTRYPOINT_LINES} limit"
                )
            if was_byte_truncated:
                reasons.append(
                    f"{original_byte_count} bytes > {MAX_ENTRYPOINT_BYTES} limit"
                )
            content += (
                f"\n\n> WARNING: MEMORY.md was truncated ({', '.join(reasons)}). "
                "Consider pruning old entries."
            )

        return EntrypointResult(
            content=content,
            line_count=min(original_line_count, MAX_ENTRYPOINT_LINES),
            byte_count=min(original_byte_count, MAX_ENTRYPOINT_BYTES),
            was_line_truncated=was_line_truncated,
            was_byte_truncated=was_byte_truncated,
        )

    async def update_entrypoint(self, content: str) -> None:
        """Overwrite the MEMORY.md index."""
        path = self._dir / ENTRYPOINT_NAME
        try:
            await asyncio.to_thread(path.write_text, content, "utf-8")
            logger.debug("Updated MEMORY.md index")
        except OSError as e:
            logger.error(f"Failed to update {ENTRYPOINT_NAME}: {e}")

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #

    def is_memory_path(self, absolute_path: str) -> bool:
        """Check if an absolute path is within the memory directory."""
        try:
            Path(absolute_path).resolve().relative_to(self._dir.resolve())
            return True
        except ValueError:
            return False

    def list_filenames(self) -> list[str]:
        """List all .md filenames (excluding MEMORY.md) in the memory dir."""
        if not self._dir.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self._dir.iterdir()
            if entry.is_file()
            and entry.suffix.lower() == ".md"
            and entry.name != ENTRYPOINT_NAME
        )

    # ------------------------------------------------------------------ #
    # Migration
    # ------------------------------------------------------------------ #

    async def migrate_from_flat_files(
        self,
        data_dir: Path,
    ) -> None:
        """Migrate from old flat-file layout to directory-based memory.

        - If old-style ``MEMORY.md`` exists at data_dir root, move to memory/
        - If ``USER.md`` exists, convert to ``user_profile.md`` with frontmatter

        This is idempotent -- safe to call multiple times.
        """
        # Migrate MEMORY.md
        old_memory = data_dir / "MEMORY.md"
        new_memory = self._dir / ENTRYPOINT_NAME
        if old_memory.is_file() and not new_memory.is_file():
            try:
                content = await asyncio.to_thread(
                    old_memory.read_text, "utf-8"
                )
                await asyncio.to_thread(
                    new_memory.write_text, content, "utf-8"
                )
                logger.info(
                    f"Migrated {old_memory} -> {new_memory}"
                )
            except OSError as e:
                logger.error(f"Failed to migrate MEMORY.md: {e}")

        # Migrate USER.md -> user_profile.md
        old_user = data_dir / "USER.md"
        new_user = self._dir / "user_profile.md"
        if old_user.is_file() and not new_user.is_file():
            try:
                user_content = await asyncio.to_thread(
                    old_user.read_text, "utf-8"
                )
                if user_content.strip():
                    fm = format_frontmatter({
                        "name": "User Profile",
                        "type": "user",
                        "description": "User profile migrated from USER.md",
                    })
                    await asyncio.to_thread(
                        new_user.write_text,
                        fm + user_content,
                        "utf-8",
                    )
                    logger.info(
                        f"Migrated {old_user} -> {new_user}"
                    )
            except OSError as e:
                logger.error(f"Failed to migrate USER.md: {e}")

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _resolve(self, filename: str) -> Path:
        """Resolve a filename to an absolute path within the memory dir.

        Prevents path traversal by requiring the result is inside self._dir.
        """
        path = (self._dir / filename).resolve()
        if not str(path).startswith(str(self._dir.resolve())):
            raise ValueError(
                f"Path traversal detected: {filename!r} resolves outside "
                f"the memory directory"
            )
        return path
