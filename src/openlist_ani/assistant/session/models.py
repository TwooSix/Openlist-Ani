"""
Data models for JSONL session persistence.

Each session is stored as a ``.jsonl`` file.  Every line is a
``SessionEntry`` linked to the previous entry via ``parent_uuid``,
forming a singly-linked chain that can be walked backwards from
any leaf to reconstruct the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionEntry:
    """One line in a session ``.jsonl`` file.

    Attributes:
        type: Entry type — ``session_start``, ``user``, ``assistant``,
              ``tool``, ``system``, or ``summary``.
        uuid: Unique identifier for this entry.
        parent_uuid: UUID of the previous entry (``None`` for the first).
        timestamp: ISO 8601 creation time.
        session_id: Session this entry belongs to.
        message: Serialized :class:`Message` dict (for transcript types).
        summary: Compaction summary text (for ``summary`` type only).
        metadata: Extra metadata (cwd, model, version, …).
    """

    type: str
    uuid: str
    parent_uuid: str | None = None
    timestamp: str = ""
    session_id: str | None = None
    message: dict | None = None
    summary: str | None = None
    metadata: dict | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "type": self.type,
            "uuid": self.uuid,
        }
        if self.parent_uuid is not None:
            d["parent_uuid"] = self.parent_uuid
        if self.timestamp:
            d["timestamp"] = self.timestamp
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.message is not None:
            d["message"] = self.message
        if self.summary is not None:
            d["summary"] = self.summary
        if self.metadata is not None:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SessionEntry:
        return cls(
            type=data["type"],
            uuid=data["uuid"],
            parent_uuid=data.get("parent_uuid"),
            timestamp=data.get("timestamp", ""),
            session_id=data.get("session_id"),
            message=data.get("message"),
            summary=data.get("summary"),
            metadata=data.get("metadata"),
        )


@dataclass
class SessionInfo:
    """Summary of a session for the resume-list UI.

    Attributes:
        session_id: UUID of the session.
        file_path: Absolute path to the ``.jsonl`` file.
        start_time: ISO 8601 session start time.
        first_prompt: First user message (for display in resume list).
        message_count: Number of transcript entries.
        mtime: File modification time (seconds since epoch).
    """

    session_id: str
    file_path: Path
    start_time: str = ""
    first_prompt: str = ""
    message_count: int = 0
    mtime: float = 0.0
    metadata: dict = field(default_factory=dict)
