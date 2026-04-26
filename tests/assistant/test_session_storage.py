"""Tests for SessionStorage — JSONL-based session persistence."""

from __future__ import annotations

import json
import time

import pytest

from openlist_ani.assistant.core.models import Message, Role, ToolCall, ToolResult
from openlist_ani.assistant.session.models import SessionEntry
from openlist_ani.assistant.session.storage import SessionStorage


@pytest.fixture
def storage(tmp_path):
    """Create a SessionStorage rooted in a temp directory."""
    return SessionStorage(tmp_path / "sessions")


# ------------------------------------------------------------------ #
# Session lifecycle
# ------------------------------------------------------------------ #


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_start_new_session(self, storage: SessionStorage):
        session_id = await storage.start_new_session()
        assert session_id
        assert storage.session_id == session_id

    @pytest.mark.asyncio
    async def test_start_creates_jsonl_file(self, storage: SessionStorage):
        session_id = await storage.start_new_session()
        path = storage._session_path(session_id)
        assert path.is_file()

        # First line should be session_start
        data = json.loads(path.read_text().split("\n")[0])
        assert data["type"] == "session_start"
        assert data["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_multiple_sessions(self, storage: SessionStorage):
        s1 = await storage.start_new_session()
        s2 = await storage.start_new_session()
        assert s1 != s2
        assert storage.session_id == s2


# ------------------------------------------------------------------ #
# Record messages
# ------------------------------------------------------------------ #


class TestRecordMessage:
    @pytest.mark.asyncio
    async def test_record_user_message(self, storage: SessionStorage):
        await storage.start_new_session()
        msg = Message(role=Role.USER, content="Hello")
        uuid = await storage.record_message(msg)
        assert uuid

    @pytest.mark.asyncio
    async def test_record_assistant_message(self, storage: SessionStorage):
        await storage.start_new_session()
        msg = Message(role=Role.ASSISTANT, content="Hi there!")
        uuid = await storage.record_message(msg)
        assert uuid

    @pytest.mark.asyncio
    async def test_record_tool_message(self, storage: SessionStorage):
        await storage.start_new_session()
        msg = Message(
            role=Role.TOOL,
            tool_results=[
                ToolResult(
                    tool_call_id="tc1",
                    name="search",
                    content="results...",
                )
            ],
        )
        uuid = await storage.record_message(msg)
        assert uuid

    @pytest.mark.asyncio
    async def test_uuid_chain(self, storage: SessionStorage):
        """Each entry's parent_uuid should be the previous entry's uuid."""
        session_id = await storage.start_new_session()

        await storage.record_message(Message(role=Role.USER, content="Q1"))
        await storage.record_message(Message(role=Role.ASSISTANT, content="A1"))

        # Read file and verify chain
        entries = storage._load_entries_sync(session_id)
        # session_start -> user -> assistant
        assert len(entries) == 3
        assert entries[0].type == "session_start"
        assert entries[1].parent_uuid == entries[0].uuid
        assert entries[2].parent_uuid == entries[1].uuid

    @pytest.mark.asyncio
    async def test_record_summary(self, storage: SessionStorage):
        session_id = await storage.start_new_session()
        await storage.record_summary("Conversation about anime tracking.")

        entries = storage._load_entries_sync(session_id)
        summary_entries = [e for e in entries if e.type == "summary"]
        assert len(summary_entries) == 1
        assert summary_entries[0].summary == "Conversation about anime tracking."


# ------------------------------------------------------------------ #
# Load / Resume
# ------------------------------------------------------------------ #


class TestLoadSession:
    @pytest.mark.asyncio
    async def test_load_empty_session(self, storage: SessionStorage):
        session_id = await storage.start_new_session()
        messages = await storage.load_session(session_id)
        assert messages == []  # session_start has no message

    @pytest.mark.asyncio
    async def test_load_conversation(self, storage: SessionStorage):
        session_id = await storage.start_new_session()
        await storage.record_message(Message(role=Role.USER, content="Q1"))
        await storage.record_message(Message(role=Role.ASSISTANT, content="A1"))
        await storage.record_message(Message(role=Role.USER, content="Q2"))
        await storage.record_message(Message(role=Role.ASSISTANT, content="A2"))

        messages = await storage.load_session(session_id)
        assert len(messages) == 4
        assert messages[0].role == Role.USER
        assert messages[0].content == "Q1"
        assert messages[3].role == Role.ASSISTANT
        assert messages[3].content == "A2"

    @pytest.mark.asyncio
    async def test_load_preserves_tool_calls(self, storage: SessionStorage):
        session_id = await storage.start_new_session()
        tc = ToolCall(id="tc1", name="search", arguments={"q": "one piece"})
        msg = Message(role=Role.ASSISTANT, content="Searching...", tool_calls=[tc])
        await storage.record_message(msg)

        messages = await storage.load_session(session_id)
        assert len(messages) == 1
        assert len(messages[0].tool_calls) == 1
        assert messages[0].tool_calls[0].name == "search"
        assert messages[0].tool_calls[0].arguments == {"q": "one piece"}

    @pytest.mark.asyncio
    async def test_load_nonexistent_session(self, storage: SessionStorage):
        messages = await storage.load_session("nonexistent-uuid")
        assert messages == []


# ------------------------------------------------------------------ #
# Switch session (resume)
# ------------------------------------------------------------------ #


class TestSwitchSession:
    @pytest.mark.asyncio
    async def test_switch_and_append(self, storage: SessionStorage):
        # Create session 1
        s1 = await storage.start_new_session()
        await storage.record_message(Message(role=Role.USER, content="Q1"))
        await storage.record_message(Message(role=Role.ASSISTANT, content="A1"))

        # Create session 2
        await storage.start_new_session()
        await storage.record_message(Message(role=Role.USER, content="Q2"))

        # Switch back to session 1 and append
        await storage.switch_session(s1)
        await storage.record_message(Message(role=Role.USER, content="Q1b"))
        await storage.record_message(Message(role=Role.ASSISTANT, content="A1b"))

        # Verify session 1 has all messages in chain
        messages = await storage.load_session(s1)
        assert len(messages) == 4
        assert messages[0].content == "Q1"
        assert messages[3].content == "A1b"


# ------------------------------------------------------------------ #
# List sessions
# ------------------------------------------------------------------ #


class TestListSessions:
    @pytest.mark.asyncio
    async def test_list_empty(self, storage: SessionStorage):
        sessions = await storage.list_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    async def test_list_sessions(self, storage: SessionStorage):
        s1 = await storage.start_new_session({"model": "test"})
        await storage.record_message(Message(role=Role.USER, content="Hello"))
        await storage.record_message(Message(role=Role.ASSISTANT, content="Hi!"))

        # Backdate s1's file so s2 is strictly newer without a real sleep.
        import os

        s1_path = storage._sessions_dir / f"{s1}.jsonl"
        old_time = time.time() - 10
        os.utime(s1_path, (old_time, old_time))

        s2 = await storage.start_new_session()
        await storage.record_message(Message(role=Role.USER, content="Another session"))

        sessions = await storage.list_sessions()
        assert len(sessions) == 2
        # Most recent first
        assert sessions[0].session_id == s2
        assert sessions[1].session_id == s1
        assert sessions[1].first_prompt == "Hello"
        assert sessions[1].message_count == 3  # start + user + assistant


# ------------------------------------------------------------------ #
# Cleanup
# ------------------------------------------------------------------ #


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_old_sessions(self, storage: SessionStorage, tmp_path):
        # Create a session and backdate it
        s1 = await storage.start_new_session()
        # Add a user message so the session is not considered empty
        await storage.record_message(Message(role=Role.USER, content="hello"))
        path = storage._session_path(s1)
        storage.close()

        # Set mtime to 60 days ago
        old_time = time.time() - (60 * 86_400)
        import os

        os.utime(path, (old_time, old_time))

        # Create a recent session (also with a real message)
        s2 = await storage.start_new_session()
        await storage.record_message(Message(role=Role.USER, content="world"))

        removed = await storage.cleanup_old_sessions(max_age_days=30)
        assert removed == 1

        sessions = await storage.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == s2

    @pytest.mark.asyncio
    async def test_cleanup_nothing_old(self, storage: SessionStorage):
        await storage.start_new_session()
        removed = await storage.cleanup_old_sessions(max_age_days=30)
        assert removed == 0


# ------------------------------------------------------------------ #
# SessionEntry serialization
# ------------------------------------------------------------------ #


class TestSessionEntry:
    def test_to_dict_minimal(self):
        entry = SessionEntry(type="user", uuid="abc")
        d = entry.to_dict()
        assert d == {"type": "user", "uuid": "abc"}

    def test_to_dict_full(self):
        entry = SessionEntry(
            type="assistant",
            uuid="abc",
            parent_uuid="xyz",
            timestamp="2026-04-09T10:00:00",
            session_id="sess1",
            message={"role": "assistant", "content": "hi"},
        )
        d = entry.to_dict()
        assert d["parent_uuid"] == "xyz"
        assert d["message"]["content"] == "hi"

    def test_roundtrip(self):
        entry = SessionEntry(
            type="summary",
            uuid="abc",
            parent_uuid="xyz",
            timestamp="2026-04-09T10:00:00",
            session_id="sess1",
            summary="A summary",
            metadata={"version": "1.0"},
        )
        d = entry.to_dict()
        restored = SessionEntry.from_dict(d)
        assert restored.type == entry.type
        assert restored.uuid == entry.uuid
        assert restored.summary == entry.summary
        assert restored.metadata == entry.metadata
