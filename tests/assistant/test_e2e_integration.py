"""
End-to-end integration test that wires up the real production components:
  MemoryManager → SessionStorage → ContextBuilder → AgenticLoop → AutoDreamRunner

Uses a MockProvider so no real LLM API is needed, but everything else is real:
  - Real filesystem I/O (JSONL, memory files, lock files)
  - Real MemoryDir with MEMORY.md index
  - Real SessionStorage with UUID chains
  - Real ContextBuilder with SOUL.md + memory prompt
  - Real AgenticLoop with process() event streaming
  - Real AutoDreamRunner gate checks + consolidation lock
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.core.models import (
    EventType,
    LoopEvent,
    Message,
    ProviderResponse,
    Role,
)
from openlist_ani.assistant.dream.config import AutoDreamConfig
from openlist_ani.assistant.dream.runner import AutoDreamRunner
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.session.storage import SessionStorage
from openlist_ani.assistant.provider.base import Provider
from openlist_ani.assistant.tool.base import BaseTool
from openlist_ani.assistant.tool.registry import ToolRegistry


class MockProvider(Provider):
    """Mock provider that returns pre-configured responses (standalone copy)."""

    def __init__(self, responses: list[ProviderResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0

    async def chat_completion(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_tokens_override: int | None = None,
        temperature: float | None = None,
    ) -> ProviderResponse:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return ProviderResponse(text="Default mock response.")

    async def chat_completion_stream(self, messages, tools=None, **kw):
        response = await self.chat_completion(messages, tools)
        if response.text:
            yield ProviderResponse(text=response.text)
        yield ProviderResponse(
            tool_calls=response.tool_calls,
            stop_reason=response.stop_reason or "stop",
        )

    def format_tool_definitions(self, tools: list[BaseTool]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Fresh data directory for each test."""
    d = tmp_path / "data" / "assistant"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


@pytest.fixture
def memory(data_dir: Path, project_root: Path) -> MemoryManager:
    return MemoryManager(data_dir=data_dir, project_root=project_root)


@pytest.fixture
def session_storage(data_dir: Path) -> SessionStorage:
    return SessionStorage(data_dir / "sessions")


@pytest.fixture
def dream_runner(data_dir: Path) -> AutoDreamRunner:
    """AutoDreamRunner with a MockProvider (dream LLM calls are mocked)."""
    provider = MockProvider(
        [ProviderResponse(text="Dream consolidation complete. No changes needed.")]
    )
    return AutoDreamRunner(
        config=AutoDreamConfig(enabled=True, min_hours=0, min_sessions=1),
        provider=provider,
        memory_dir=data_dir / "memory",
        sessions_dir=data_dir / "sessions",
        data_dir=data_dir,
    )


def _build_loop(
    memory: MemoryManager,
    session_storage: SessionStorage,
    dream_runner: AutoDreamRunner,
    responses: list[ProviderResponse] | None = None,
) -> tuple[AgenticLoop, MockProvider]:
    """Build a real AgenticLoop with all production components wired together."""
    provider = MockProvider(
        responses
        or [
            ProviderResponse(text="Hello! I'm your assistant."),
        ]
    )
    registry = ToolRegistry()
    context = ContextBuilder(
        memory,
        model_name="mock-model",
        provider_type="mock",
        tools=registry.all_tools(),
    )
    loop = AgenticLoop(
        provider=provider,
        registry=registry,
        context=context,
        memory=memory,
        session_storage=session_storage,
        auto_dream_runner=dream_runner,
    )
    return loop, provider


async def _collect_events(loop: AgenticLoop, text: str) -> list[LoopEvent]:
    """Run loop.process() and collect all events."""
    events: list[LoopEvent] = []
    async for event in loop.process(text):
        events.append(event)
    return events


# ── Test: Full conversation lifecycle ─────────────────────────────


class TestE2EConversation:
    """Test a complete conversation lifecycle: start session → send messages →
    verify JSONL persistence → /clear → new session → verify."""

    @pytest.mark.asyncio
    async def test_startup_creates_data_structure(
        self, memory: MemoryManager, data_dir: Path
    ):
        """Startup should create SOUL.md, memory/ dir."""
        assert (data_dir / "SOUL.md").exists()
        assert (data_dir / "memory").is_dir()

    @pytest.mark.asyncio
    async def test_session_start_creates_jsonl(
        self, session_storage: SessionStorage, data_dir: Path
    ):
        """start_new_session should create a JSONL file with session_start entry."""
        session_id = await session_storage.start_new_session(
            metadata={"model": "test-model"}
        )

        jsonl_path = data_dir / "sessions" / f"{session_id}.jsonl"
        assert jsonl_path.exists()

        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["type"] == "session_start"
        assert entry["session_id"] == session_id
        assert entry["metadata"]["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_full_conversation_persists_to_jsonl(
        self,
        memory: MemoryManager,
        session_storage: SessionStorage,
        dream_runner: AutoDreamRunner,
        data_dir: Path,
    ):
        """Send a user message through the loop and verify JSONL records
        both the user message and the assistant response."""
        session_id = await session_storage.start_new_session(metadata={"model": "mock"})

        loop, _provider = _build_loop(memory, session_storage, dream_runner)

        # Process a user turn
        events = await _collect_events(loop, "Hello, how are you?")

        # Verify events contain THINKING, TEXT_DELTA, TEXT_DONE
        event_types = [e.type for e in events]
        assert EventType.THINKING in event_types
        assert EventType.TEXT_DONE in event_types

        # Find TEXT_DONE to get the response text
        text_done = [e for e in events if e.type == EventType.TEXT_DONE]
        assert len(text_done) >= 1
        assert text_done[0].text == "Hello! I'm your assistant."

        # Verify JSONL has session_start + user + assistant entries
        jsonl_path = data_dir / "sessions" / f"{session_id}.jsonl"
        lines = [line for line in jsonl_path.read_text().strip().split("\n") if line]
        assert len(lines) == 3  # session_start, user, assistant

        entries = [json.loads(line) for line in lines]
        assert entries[0]["type"] == "session_start"
        assert entries[1]["type"] == "user"
        assert entries[1]["message"]["content"] == "Hello, how are you?"
        assert entries[2]["type"] == "assistant"
        assert entries[2]["message"]["content"] == "Hello! I'm your assistant."

        # Verify UUID chain: each entry's parent_uuid == previous entry's uuid
        assert entries[1]["parent_uuid"] == entries[0]["uuid"]
        assert entries[2]["parent_uuid"] == entries[1]["uuid"]

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(
        self,
        memory: MemoryManager,
        session_storage: SessionStorage,
        dream_runner: AutoDreamRunner,
        data_dir: Path,
    ):
        """Multiple turns should accumulate in the same session JSONL."""
        session_id = await session_storage.start_new_session()

        loop, _provider = _build_loop(
            memory,
            session_storage,
            dream_runner,
            responses=[
                ProviderResponse(text="First response."),
                ProviderResponse(text="Second response."),
                ProviderResponse(text="Third response."),
            ],
        )

        await _collect_events(loop, "Turn 1")
        await _collect_events(loop, "Turn 2")
        await _collect_events(loop, "Turn 3")

        # Verify JSONL: 1 session_start + 3 user + 3 assistant = 7
        jsonl_path = data_dir / "sessions" / f"{session_id}.jsonl"
        lines = [line for line in jsonl_path.read_text().strip().split("\n") if line]
        assert len(lines) == 7

        entries = [json.loads(line) for line in lines]
        types = [e["type"] for e in entries]
        assert types == [
            "session_start",
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
            "assistant",
        ]

        # Verify full UUID chain integrity
        for i in range(1, len(entries)):
            assert (
                entries[i]["parent_uuid"] == entries[i - 1]["uuid"]
            ), f"Chain broken at index {i}"

    @pytest.mark.asyncio
    async def test_session_resume_loads_messages(
        self,
        memory: MemoryManager,
        session_storage: SessionStorage,
        dream_runner: AutoDreamRunner,
        data_dir: Path,
    ):
        """Resume should load the message chain and allow continuing."""
        # Create first session with some messages
        session_id = await session_storage.start_new_session()

        loop1, _ = _build_loop(
            memory,
            session_storage,
            dream_runner,
            responses=[ProviderResponse(text="Hello from session 1.")],
        )
        await _collect_events(loop1, "First message")

        # Now create a new loop and resume the session
        loop2, _ = _build_loop(
            memory,
            session_storage,
            dream_runner,
            responses=[ProviderResponse(text="Continuing session 1.")],
        )
        await loop2.resume(session_id)

        # The resumed loop should have messages from the original session
        # (system + user + assistant from session 1 + resume note)
        # Count non-system messages in the loop's internal state
        loaded = await session_storage.load_session(session_id)
        assert len(loaded) >= 2  # user + assistant at minimum

        # Send another message on the resumed session
        events = await _collect_events(loop2, "Continuing...")
        text_done = [e for e in events if e.type == EventType.TEXT_DONE]
        assert text_done[0].text == "Continuing session 1."


# ── Test: /clear creates new session ──────────────────────────────


class TestE2EClearCommand:
    """Test that /clear resets the loop and starts a new session."""

    @pytest.mark.asyncio
    async def test_clear_creates_new_session(
        self,
        memory: MemoryManager,
        session_storage: SessionStorage,
        dream_runner: AutoDreamRunner,
        data_dir: Path,
    ):
        """After /clear, a new session JSONL should be created."""
        session_id_1 = await session_storage.start_new_session()

        loop, _ = _build_loop(
            memory,
            session_storage,
            dream_runner,
            responses=[
                ProviderResponse(text="Before clear."),
                ProviderResponse(text="After clear."),
            ],
        )
        await _collect_events(loop, "Hello before clear")

        # Simulate /clear: reset loop + start new session
        loop.reset()
        session_id_2 = await session_storage.start_new_session()

        assert session_id_1 != session_id_2

        # Both JSONL files should exist
        assert (data_dir / "sessions" / f"{session_id_1}.jsonl").exists()
        assert (data_dir / "sessions" / f"{session_id_2}.jsonl").exists()

        # Send a message in the new session
        await _collect_events(loop, "Hello after clear")

        # New session should have: session_start + user + assistant
        jsonl_path = data_dir / "sessions" / f"{session_id_2}.jsonl"
        lines = [line for line in jsonl_path.read_text().strip().split("\n") if line]
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_clear_does_not_corrupt_old_session(
        self,
        memory: MemoryManager,
        session_storage: SessionStorage,
        dream_runner: AutoDreamRunner,
        data_dir: Path,
    ):
        """Old session should remain intact after /clear."""
        session_id_1 = await session_storage.start_new_session()

        loop, _ = _build_loop(
            memory,
            session_storage,
            dream_runner,
            responses=[ProviderResponse(text="Old response.")],
        )
        await _collect_events(loop, "Old message")

        # Read old session content before clear
        old_content = (data_dir / "sessions" / f"{session_id_1}.jsonl").read_text()

        # /clear
        loop.reset()
        await session_storage.start_new_session()

        # Old session should be unchanged
        assert (
            data_dir / "sessions" / f"{session_id_1}.jsonl"
        ).read_text() == old_content


# ── Test: Memory directory operations ─────────────────────────────


class TestE2EMemory:
    """Test memory directory operations through the real MemoryManager."""

    @pytest.mark.asyncio
    async def test_memory_prompt_in_system_message(self, memory: MemoryManager):
        """The system prompt should include memory instructions."""
        prompt = memory.build_memory_prompt()
        assert "# Memory" in prompt
        assert "Memory types" in prompt
        assert "No memories stored yet" in prompt

    @pytest.mark.asyncio
    async def test_write_and_read_memory(self, memory: MemoryManager):
        """Writing a memory file should be reflected in the memory prompt."""
        mem_dir = memory.memory_dir

        # Write a memory file
        await mem_dir.write_memory(
            "user_prefs.md",
            (
                "---\n"
                "name: User Preferences\n"
                "type: user\n"
                "description: Coding preferences\n"
                "---\n"
                "- Prefers Python\n"
                "- Uses vim\n"
            ),
        )

        # Update the index
        await mem_dir.update_entrypoint(
            "- [User Preferences](user_prefs.md) — coding preferences\n"
        )

        # The memory prompt should now contain the index content
        prompt = memory.build_memory_prompt()
        assert "User Preferences" in prompt
        assert "user_prefs.md" in prompt

        # Read back the file
        content = mem_dir.read_memory("user_prefs.md")
        assert "Prefers Python" in content
        assert "Uses vim" in content

    @pytest.mark.asyncio
    async def test_memory_in_context_builder(self, memory: MemoryManager):
        """ContextBuilder should include memory prompt in the system message."""
        mem_dir = memory.memory_dir
        await mem_dir.write_memory(
            "test.md", ("---\nname: Test\ntype: reference\n---\nTest content\n")
        )
        await mem_dir.update_entrypoint("- [Test](test.md) — test reference\n")

        context = ContextBuilder(
            memory,
            model_name="mock",
            provider_type="mock",
        )
        messages = context.build("Hi")

        system_content = messages[0].content
        assert "# Memory" in system_content
        assert "Test" in system_content

    @pytest.mark.asyncio
    async def test_delete_memory(self, memory: MemoryManager):
        """Deleting a memory file should work."""
        mem_dir = memory.memory_dir
        await mem_dir.write_memory(
            "temp.md", "---\nname: Temp\ntype: user\n---\nTemp\n"
        )

        assert "Temp" in mem_dir.read_memory("temp.md")

        await mem_dir.delete_memory("temp.md")

        # Should return empty or raise
        content = mem_dir.read_memory("temp.md")
        assert content == ""

    @pytest.mark.asyncio
    async def test_memory_scan(self, memory: MemoryManager):
        """scan_memory_files should list all memory files."""
        mem_dir = memory.memory_dir
        await mem_dir.write_memory("a.md", "---\nname: A\ntype: user\n---\ncontent A\n")
        await mem_dir.write_memory(
            "b.md", "---\nname: B\ntype: project\n---\ncontent B\n"
        )

        headers = await mem_dir.scan_memory_files()
        names = {h.filename for h in headers}
        assert "a.md" in names
        assert "b.md" in names


# ── Test: Auto-dream gate checks ─────────────────────────────────


class TestE2EDream:
    """Test the auto-dream runner gate checks with real filesystem state."""

    @pytest.mark.asyncio
    async def test_dream_force_run_no_sessions(self, dream_runner: AutoDreamRunner):
        """force_run with no sessions should return 'no sessions'."""
        result = await dream_runner.force_run()
        assert result is not None
        assert "No sessions" in result.summary

    @pytest.mark.asyncio
    async def test_dream_force_run_with_sessions(
        self,
        dream_runner: AutoDreamRunner,
        session_storage: SessionStorage,
        data_dir: Path,
    ):
        """force_run with existing sessions should run consolidation."""
        # Create a completed session
        await session_storage.start_new_session()
        await session_storage.record_message(
            Message(role=Role.USER, content="test message")
        )
        await session_storage.record_message(
            Message(role=Role.ASSISTANT, content="test response")
        )
        session_storage.close()

        result = await dream_runner.force_run()
        assert result is not None
        # The mock provider returns "Dream consolidation complete" with no tool calls
        # so it should complete successfully
        assert result.sessions_reviewed >= 1

    @pytest.mark.asyncio
    async def test_dream_lock_file(
        self,
        dream_runner: AutoDreamRunner,
        session_storage: SessionStorage,
        data_dir: Path,
    ):
        """After a successful dream run, the lock file should be updated."""
        await session_storage.start_new_session()
        await session_storage.record_message(Message(role=Role.USER, content="msg"))
        session_storage.close()

        await dream_runner.force_run()

        lock_file = data_dir / ".consolidate-lock"
        assert lock_file.exists()

    @pytest.mark.asyncio
    async def test_maybe_run_gate_disabled(self, data_dir: Path):
        """maybe_run should return None when disabled."""
        provider = MockProvider()
        runner = AutoDreamRunner(
            config=AutoDreamConfig(enabled=False),
            provider=provider,
            memory_dir=data_dir / "memory",
            sessions_dir=data_dir / "sessions",
            data_dir=data_dir,
        )
        result = await runner.maybe_run("some-session-id")
        assert result is None


# ── Test: Session listing and cleanup ─────────────────────────────


class TestE2ESessionManagement:
    """Test session listing and cleanup."""

    @pytest.mark.asyncio
    async def test_list_sessions(self, session_storage: SessionStorage, data_dir: Path):
        """list_sessions should return all sessions sorted by mtime."""
        sid1 = await session_storage.start_new_session()
        await session_storage.record_message(
            Message(role=Role.USER, content="Session 1 first msg")
        )

        sid2 = await session_storage.start_new_session()
        await session_storage.record_message(
            Message(role=Role.USER, content="Session 2 first msg")
        )

        sessions = await session_storage.list_sessions()
        assert len(sessions) >= 2

        # Most recent should be first
        session_ids = [s.session_id for s in sessions]
        assert sid2 in session_ids
        assert sid1 in session_ids

    @pytest.mark.asyncio
    async def test_load_session_chain(self, session_storage: SessionStorage):
        """load_session should reconstruct the UUID chain as Messages."""
        sid = await session_storage.start_new_session()
        await session_storage.record_message(Message(role=Role.USER, content="Hello"))
        await session_storage.record_message(
            Message(role=Role.ASSISTANT, content="Hi there")
        )
        await session_storage.record_message(
            Message(role=Role.USER, content="How are you?")
        )
        await session_storage.record_message(
            Message(role=Role.ASSISTANT, content="I'm good!")
        )

        messages = await session_storage.load_session(sid)
        assert len(messages) == 4
        assert messages[0].role == Role.USER
        assert messages[0].content == "Hello"
        assert messages[1].role == Role.ASSISTANT
        assert messages[1].content == "Hi there"
        assert messages[2].role == Role.USER
        assert messages[2].content == "How are you?"
        assert messages[3].role == Role.ASSISTANT
        assert messages[3].content == "I'm good!"


# ── Test: Context builder full integration ────────────────────────


class TestE2EContextBuilder:
    """Test that the full context builder produces a valid system prompt
    with all sections when memory and CLAUDE.md are present."""

    @pytest.mark.asyncio
    async def test_full_system_prompt_assembly(
        self, memory: MemoryManager, project_root: Path
    ):
        """System prompt should contain SOUL.md, memory, environment."""
        # Write a project CLAUDE.md
        (project_root / "CLAUDE.md").write_text("Always use type hints.\n")

        # Write some memory
        mem_dir = memory.memory_dir
        await mem_dir.write_memory(
            "prefs.md", ("---\nname: Prefs\ntype: user\n---\n- Dark mode\n")
        )
        await mem_dir.update_entrypoint("- [Prefs](prefs.md) — user preferences\n")

        context = ContextBuilder(
            memory,
            model_name="gpt-4o",
            provider_type="openai",
        )
        messages = context.build("Test prompt")

        assert len(messages) == 2
        assert messages[0].role == Role.SYSTEM
        assert messages[1].role == Role.USER
        assert messages[1].content == "Test prompt"

        sys_content = messages[0].content

        # SOUL.md content
        assert "Openlist-Ani Assistant" in sys_content

        # Memory instructions + index
        assert "# Memory" in sys_content
        assert "Prefs" in sys_content

        # CLAUDE.md
        assert "type hints" in sys_content

        # Environment
        assert "gpt-4o" in sys_content
        assert "openai" in sys_content


# ── Test: Data migration ──────────────────────────────────────────


class TestE2EMigration:
    """Test old flat-file → directory-based migration."""

    @pytest.mark.asyncio
    async def test_migrate_old_memory_md(self, tmp_path: Path):
        """Old MEMORY.md in data root should migrate to memory/MEMORY.md."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        # Simulate old layout: MEMORY.md in data root
        (data_dir / "MEMORY.md").write_text(
            "- Old memory entry 1\n- Old memory entry 2\n"
        )

        mm = MemoryManager(data_dir=data_dir, project_root=project)
        await mm.migrate_if_needed()

        # New file should exist with migrated content
        assert (data_dir / "memory" / "MEMORY.md").exists()
        content = (data_dir / "memory" / "MEMORY.md").read_text()
        assert "Old memory entry 1" in content

    @pytest.mark.asyncio
    async def test_migrate_old_user_md(self, tmp_path: Path):
        """Old USER.md should migrate to memory/user_profile.md with frontmatter."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        (data_dir / "USER.md").write_text("Likes Python\nUses Linux\n")

        mm = MemoryManager(data_dir=data_dir, project_root=project)
        await mm.migrate_if_needed()

        migrated = data_dir / "memory" / "user_profile.md"
        assert migrated.exists()
        content = migrated.read_text()
        assert "Likes Python" in content
        assert "type: user" in content  # frontmatter added
