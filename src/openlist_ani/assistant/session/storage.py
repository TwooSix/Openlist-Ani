"""
JSONL-based session storage with UUID chain.

Each session is one ``.jsonl`` file under ``sessions/``.  Entries are
linked by ``parent_uuid`` forming a singly-linked chain that can be
walked from any leaf back to the session start.

Thread-safety: all public methods are ``async`` and file I/O is
offloaded to ``asyncio.to_thread`` so the event loop never blocks.
"""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from loguru import logger

from openlist_ani.assistant.core.models import Message, Role
from openlist_ani.assistant.session.models import SessionEntry, SessionInfo


class SessionStorage:
    """JSONL-based session persistence with UUID chain."""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._session_id: str = ""
        self._last_uuid: str | None = None
        self._file_handle: IO[str] | None = None

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #

    async def start_new_session(self, metadata: dict | None = None) -> str:
        """Create a new session.  Returns the new session_id (UUID)."""
        self._close_handle()
        self._session_id = str(_uuid.uuid4())
        self._last_uuid = None

        # Record session_start entry
        await self._record_session_start(metadata or {})
        logger.info(f"Started new session: {self._session_id}")
        return self._session_id

    async def switch_session(self, session_id: str) -> None:
        """Switch to an existing session for appending (resume)."""
        self._close_handle()
        self._session_id = session_id

        # Find the last entry UUID so new entries chain correctly
        entries = await self._load_entries(session_id)
        if entries:
            self._last_uuid = entries[-1].uuid
        else:
            self._last_uuid = None

        logger.info(f"Switched to session: {session_id}")

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #

    async def record_message(self, message: Message) -> str:
        """Record a message, auto-detecting type from role.  Returns UUID."""
        entry_type = self._role_to_entry_type(message.role)
        entry_uuid = str(_uuid.uuid4())

        entry = SessionEntry(
            type=entry_type,
            uuid=entry_uuid,
            parent_uuid=self._last_uuid,
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=self._session_id,
            message=message.to_dict(),
        )
        await self._append_entry(entry)
        self._last_uuid = entry_uuid
        return entry_uuid

    async def record_summary(self, summary: str) -> None:
        """Record a compaction summary entry (marks a compact boundary)."""
        entry_uuid = str(_uuid.uuid4())
        entry = SessionEntry(
            type="summary",
            uuid=entry_uuid,
            parent_uuid=self._last_uuid,
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=self._session_id,
            summary=summary,
        )
        await self._append_entry(entry)
        self._last_uuid = entry_uuid

    # ------------------------------------------------------------------ #
    # Read / Resume
    # ------------------------------------------------------------------ #

    async def load_session(self, session_id: str) -> list[Message]:
        """Load the complete message chain for a session.

        Algorithm:
        1. Parse all entries from the JSONL file
        2. Find the latest leaf, walk ``parent_uuid`` chain backwards
        3. Convert transcript entries to :class:`Message` objects
        """
        entries = await self._load_entries(session_id)
        if not entries:
            return []

        chain = self._find_chain(entries)
        return self._entries_to_messages(chain)

    @staticmethod
    def _find_chain(entries: list[SessionEntry]) -> list[SessionEntry]:
        """Walk the UUID chain from the latest leaf back to the root."""
        child_set: set[str] = set()
        by_uuid: dict[str, SessionEntry] = {}
        for e in entries:
            by_uuid[e.uuid] = e
            if e.parent_uuid:
                child_set.add(e.parent_uuid)

        leaves = [e for e in entries if e.uuid not in child_set] or [entries[-1]]
        leaf = max(leaves, key=lambda e: e.timestamp or "")

        chain: list[SessionEntry] = []
        current: SessionEntry | None = leaf
        visited: set[str] = set()

        while current is not None:
            if current.uuid in visited:
                break  # Cycle guard
            visited.add(current.uuid)
            chain.append(current)
            current = by_uuid.get(current.parent_uuid) if current.parent_uuid else None

        chain.reverse()
        return chain

    @staticmethod
    def _entries_to_messages(chain: list[SessionEntry]) -> list[Message]:
        """Convert transcript entries to Message objects."""
        _TRANSCRIPT_TYPES = ("user", "assistant", "tool", "system")
        messages: list[Message] = []
        for entry in chain:
            if entry.message is not None and entry.type in _TRANSCRIPT_TYPES:
                try:
                    messages.append(Message.from_dict(entry.message))
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping malformed entry {entry.uuid}: {e}")
        return messages

    async def list_sessions(self) -> list[SessionInfo]:
        """List all available sessions sorted by mtime (most recent first).

        Reads only the first few lines of each JSONL file to extract
        metadata and the first user prompt.
        """
        return await asyncio.to_thread(self._list_sessions_sync)

    def _list_sessions_sync(self) -> list[SessionInfo]:
        sessions: list[SessionInfo] = []

        if not self._sessions_dir.is_dir():
            return sessions

        for path in self._sessions_dir.glob("*.jsonl"):
            try:
                stat = path.stat()
                info = self._parse_session_info(path, stat.st_mtime)
                if info:
                    sessions.append(info)
            except OSError as e:
                logger.warning(f"Skipping unreadable session file {path}: {e}")

        # Filter out empty sessions (only contain session_start, no real messages)
        sessions = [s for s in sessions if s.message_count > 1]
        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions

    def _parse_session_info(self, path: Path, mtime: float) -> SessionInfo | None:
        """Parse session metadata from the first few lines of a JSONL file."""
        session_id = path.stem  # Filename without extension = session_id
        start_time = ""
        first_prompt = ""
        message_count = 0
        metadata: dict = {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                for data in self._iter_jsonl_records(f):
                    message_count += 1

                    if data.get("type") == "session_start":
                        start_time = data.get("timestamp", "")
                        sid = data.get("session_id")
                        if sid:
                            session_id = sid
                        metadata = data.get("metadata", {})

                    if not first_prompt:
                        first_prompt = self._extract_first_prompt(data)
        except OSError:
            return None

        return SessionInfo(
            session_id=session_id,
            file_path=path,
            start_time=start_time,
            first_prompt=first_prompt,
            message_count=message_count,
            mtime=mtime,
            metadata=metadata,
        )

    @staticmethod
    def _iter_jsonl_records(f: Iterable[str]) -> Iterator[dict]:
        """Yield parsed JSON dicts from a JSONL file, skipping bad lines."""
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    @staticmethod
    def _extract_first_prompt(data: dict) -> str:
        """Extract the first user prompt from a JSONL record.

        Returns an empty string if *data* is not a user message or
        has no content.
        """
        if data.get("type") != "user" or not data.get("message"):
            return ""
        content = data["message"].get("content", "")
        # Strip skill injection wrapper if present
        # Format: <command-name>/X</command-name>\n<skill ...>
        #         ...</skill>\n\nACTUAL_MESSAGE
        if content.startswith("<command-name>"):
            skill_end = content.find("</skill>")
            if skill_end != -1:
                content = content[skill_end + len("</skill>") :].strip()
        return content[:100]  # Truncate for display

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #

    async def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """Remove session files older than *max_age_days*.  Returns count removed."""
        return await asyncio.to_thread(self._cleanup_sync, max_age_days)

    def _cleanup_sync(self, max_age_days: int) -> int:
        import time

        cutoff = time.time() - (max_age_days * 86_400)
        removed = 0

        if not self._sessions_dir.is_dir():
            return 0

        for path in self._sessions_dir.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as e:
                logger.warning(f"Failed to remove old session {path}: {e}")

        if removed:
            logger.info(f"Cleaned up {removed} old session file(s)")
        return removed

    # ------------------------------------------------------------------ #
    # Close
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Close the file handle.  Safe to call multiple times."""
        self._close_handle()

    def __del__(self) -> None:
        self._close_handle()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _session_path(self, session_id: str | None = None) -> Path:
        sid = session_id or self._session_id
        return self._sessions_dir / f"{sid}.jsonl"

    def _get_handle(self) -> IO[str]:
        """Get or open the file handle for the current session."""
        if self._file_handle is None or self._file_handle.closed:
            path = self._session_path()
            self._file_handle = open(path, "a", encoding="utf-8")  # noqa: SIM115
        return self._file_handle

    def _close_handle(self) -> None:
        if self._file_handle is not None and not self._file_handle.closed:
            self._file_handle.close()
        self._file_handle = None

    async def _append_entry(self, entry: SessionEntry) -> None:
        """Serialize and append an entry to the current session file."""
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        await asyncio.to_thread(self._write_line, line)

    def _write_line(self, line: str) -> None:
        handle = self._get_handle()
        handle.write(line)
        handle.flush()

    async def _record_session_start(self, metadata: dict) -> None:
        entry_uuid = str(_uuid.uuid4())
        entry = SessionEntry(
            type="session_start",
            uuid=entry_uuid,
            parent_uuid=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=self._session_id,
            metadata={
                **metadata,
                "cwd": str(Path.cwd()),
            },
        )
        await self._append_entry(entry)
        self._last_uuid = entry_uuid

    async def _load_entries(self, session_id: str) -> list[SessionEntry]:
        """Load all entries from a session JSONL file."""
        return await asyncio.to_thread(self._load_entries_sync, session_id)

    def _load_entries_sync(self, session_id: str) -> list[SessionEntry]:
        path = self._session_path(session_id)
        if not path.is_file():
            return []

        entries: list[SessionEntry] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entries.append(SessionEntry.from_dict(data))
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(
                            f"Skipping malformed entry at {path}:{line_num}: {e}"
                        )
        except OSError as e:
            logger.error(f"Failed to read session file {path}: {e}")

        return entries

    @staticmethod
    def _role_to_entry_type(role: Role) -> str:
        mapping = {
            Role.USER: "user",
            Role.ASSISTANT: "assistant",
            Role.TOOL: "tool",
            Role.SYSTEM: "system",
        }
        return mapping.get(role, "system")
