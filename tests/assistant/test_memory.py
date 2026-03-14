"""Tests for Markdown file-based assistant memory management."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openlist_ani.assistant.memory import AssistantMemoryManager


@pytest.fixture
def temp_memory_dir(tmp_path) -> Path:
    """Provide an isolated file-backed memory directory."""
    mem_dir = tmp_path / "assistant"
    mem_dir.mkdir()
    (mem_dir / "sessions").mkdir()
    return mem_dir


def _create_manager(temp_memory_dir: Path, client=None) -> AssistantMemoryManager:
    """Helper to create a manager with a temp directory."""
    return AssistantMemoryManager(
        client=client,
        model="gpt-4o",
        base_dir=temp_memory_dir,
    )


class TestAssistantMemoryManager:
    async def test_append_turn_creates_session_file(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        await manager.append_turn("Hello", "Hello! How can I help?")

        session_files = list((temp_memory_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files) == 1

        content = session_files[0].read_text(encoding="utf-8")
        assert "### Turn 1" in content
        assert "**User:** Hello" in content
        assert "**Assistant:** Hello! How can I help?" in content

    async def test_append_turn_reuses_active_session(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        await manager.append_turn("Hello", "Hi there!")
        await manager.append_turn("Search anime", "Found 5 results")

        session_files = list((temp_memory_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files) == 1

        content = session_files[0].read_text(encoding="utf-8")
        assert "### Turn 1" in content
        assert "### Turn 2" in content

    async def test_build_system_messages_with_soul(self, temp_memory_dir):
        soul_path = temp_memory_dir / "SOUL.md"
        soul_path.write_text("# Soul\n\nYou are a helpful assistant.", encoding="utf-8")

        manager = _create_manager(temp_memory_dir)
        messages = await manager.build_system_messages("test")

        assert any(
            m["role"] == "system" and "helpful assistant" in m["content"]
            for m in messages
        )

    async def test_build_system_messages_includes_memory(self, temp_memory_dir):
        memory_path = temp_memory_dir / "MEMORY.md"
        memory_path.write_text(
            "# Long-Term Memory\n\n## Summary\n\nUser likes sci-fi anime.\n\n"
            "## Facts\n\n- [preference|0.90] User prefers dubbed\n",
            encoding="utf-8",
        )

        manager = _create_manager(temp_memory_dir)
        messages = await manager.build_system_messages("recommend anime")

        memory_msg = [
            m
            for m in messages
            if m["role"] == "system" and "long-term memory" in m["content"].lower()
        ]
        assert len(memory_msg) == 1
        assert "sci-fi" in memory_msg[0]["content"]

    async def test_build_system_messages_includes_user_profile(self, temp_memory_dir):
        user_path = temp_memory_dir / "USER.md"
        user_path.write_text(
            "# User Profile\n\n## Bangumi Preferences\n\n"
            "- Preferred genres: Sci-Fi, Action\n\n"
            "## Agent Observations\n\n"
            "- [2026-03-14] User often asks about mecha anime\n",
            encoding="utf-8",
        )

        manager = _create_manager(temp_memory_dir)
        messages = await manager.build_system_messages("hi")

        user_msg = [
            m
            for m in messages
            if m["role"] == "system" and "profile" in m["content"].lower()
        ]
        assert len(user_msg) == 1
        assert "Sci-Fi" in user_msg[0]["content"]

    async def test_build_system_messages_includes_session_history(
        self, temp_memory_dir
    ):
        manager = _create_manager(temp_memory_dir)
        await manager.append_turn("search Frieren", "Found 3 results")

        messages = await manager.build_system_messages("download first one")

        # Should contain previous user/assistant messages from session
        user_msgs = [m for m in messages if m["role"] == "user"]
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert any("Frieren" in m["content"] for m in user_msgs)
        assert any("3 results" in m["content"] for m in assistant_msgs)

    async def test_build_system_messages_skips_default_content(self, temp_memory_dir):
        """Default MEMORY.md and USER.md content should NOT generate messages."""
        memory_path = temp_memory_dir / "MEMORY.md"
        memory_path.write_text(
            "# Long-Term Memory\n\n## Summary\n\nNone\n\n## Facts\n\n- None\n",
            encoding="utf-8",
        )
        user_path = temp_memory_dir / "USER.md"
        user_path.write_text(
            "# User Profile\n\n"
            "## Bangumi Preferences\n\n"
            "（由系统自动生成，基于 Bangumi 收藏分析）\n\n"
            "## Agent Observations\n\n"
            "（由 AI 主动维护，记录与用户互动中观察到的偏好和习惯）\n",
            encoding="utf-8",
        )

        manager = _create_manager(temp_memory_dir)
        messages = await manager.build_system_messages("hello")

        # No memory or profile messages should appear
        assert not any(
            "long-term memory" in m.get("content", "").lower() for m in messages
        )
        # The first-time init prompt may contain tool name "update_user_profile",
        # but the actual "user's profile and preferences" message should NOT appear.
        assert not any(
            "the following is the user's profile" in m.get("content", "").lower()
            for m in messages
        )

    async def test_start_new_session(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        await manager.append_turn("Hello", "Hi!")
        session_files_before = list((temp_memory_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files_before) == 1

        await manager.start_new_session()
        await manager.append_turn("New topic", "Sure, what topic?")

        session_files_after = list((temp_memory_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files_after) == 2

        # Old session should be marked closed
        old_content = session_files_before[0].read_text(encoding="utf-8")
        assert "status: closed" in old_content

    async def test_clear_all_memory(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        # Set up some data
        await manager.append_turn("Hello", "Hi!")
        memory_path = temp_memory_dir / "MEMORY.md"
        memory_path.write_text(
            "# Long-Term Memory\n\n## Summary\n\nSome summary.\n\n"
            "## Facts\n\n- [preference|0.90] Some fact\n",
            encoding="utf-8",
        )

        await manager.clear_all_memory()

        # Sessions should be gone
        session_files = list((temp_memory_dir / "sessions").glob("SESSION_*.md"))
        assert len(session_files) == 0

        # MEMORY.md should be reset
        content = memory_path.read_text(encoding="utf-8")
        assert "None" in content

    async def test_update_user_profile(self, temp_memory_dir):
        user_path = temp_memory_dir / "USER.md"
        user_path.write_text(
            "# User Profile\n\n"
            "## Bangumi Preferences\n\n"
            "（由系统自动生成，基于 Bangumi 收藏分析）\n\n"
            "## Agent Observations\n\n"
            "（由 AI 主动维护，记录与用户互动中观察到的偏好和习惯）\n",
            encoding="utf-8",
        )

        manager = _create_manager(temp_memory_dir)
        await manager.update_user_profile(
            "- Preferred genres: Sci-Fi(0.9), Action(0.8)\n- Rating tendency: moderate"
        )

        content = user_path.read_text(encoding="utf-8")
        assert "Sci-Fi" in content
        assert "Agent Observations" in content  # Other section preserved

    async def test_refresh_memory_with_llm(self, temp_memory_dir):
        response_payload = {
            "summary": "User is interested in sci-fi anime.",
            "facts": [
                {
                    "content": "User prefers sci-fi anime.",
                    "category": "preference",
                    "confidence": 0.9,
                },
            ],
            "user_observations": "User tends to ask about seasonal anime.",
        }
        fake_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(response_payload, ensure_ascii=False)
                    )
                )
            ]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=fake_response)
                )
            )
        )

        manager = _create_manager(temp_memory_dir, client=fake_client)
        # Set refresh interval to 1 so it triggers immediately
        manager._REFRESH_EVERY_N_TURNS = 1

        # Create USER.md so observations can be written
        user_path = temp_memory_dir / "USER.md"
        user_path.write_text(
            manager._default_user_text(),
            encoding="utf-8",
        )

        await manager.append_turn(
            "I really like sci-fi anime",
            "Great taste! Sci-fi has amazing anime like Steins;Gate.",
        )

        # Check MEMORY.md was updated
        memory_path = temp_memory_dir / "MEMORY.md"
        assert memory_path.exists()
        memory_content = memory_path.read_text(encoding="utf-8")
        assert "sci-fi" in memory_content.lower()

        # Check USER.md observations were updated
        user_content = user_path.read_text(encoding="utf-8")
        assert "seasonal anime" in user_content

    def test_estimate_tokens(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        # Pure English
        assert manager._estimate_tokens("hello world foo bar") == 4

        # Pure CJK
        assert manager._estimate_tokens("你好世界") == 6  # 4 chars * 1.5

        # Mixed
        mixed = "hello 你好"
        tokens = manager._estimate_tokens(mixed)
        assert tokens == 4  # 1 english word + 2 CJK * 1.5

    def test_fact_parsing(self, temp_memory_dir):
        manager = _create_manager(temp_memory_dir)

        facts_text = (
            "- [preference|0.90] User likes sci-fi\n"
            "- [constraint|0.80] User only watches subtitled\n"
            "- None\n"
        )
        facts = manager._parse_fact_lines(facts_text)
        assert len(facts) == 2
        assert facts[0].content == "User likes sci-fi"
        assert facts[0].category == "preference"
        assert abs(facts[0].confidence - 0.9) < 1e-9
        assert facts[1].category == "constraint"

    def test_fact_merge_with_decay(self, temp_memory_dir):
        from openlist_ani.assistant.memory import MemoryFact

        manager = _create_manager(temp_memory_dir)

        existing = [
            MemoryFact(content="Old fact", category="general", confidence=0.5),
            MemoryFact(content="Refreshed fact", category="preference", confidence=0.8),
        ]
        new = [
            MemoryFact(content="Refreshed fact", category="preference", confidence=0.9),
            MemoryFact(content="New fact", category="workflow", confidence=0.7),
        ]

        merged = manager._merge_facts(existing, new)

        contents = {f.content for f in merged}
        assert "Refreshed fact" in contents
        assert "New fact" in contents
        # Old fact should have decayed from 0.5 to 0.4
        old = next((f for f in merged if f.content == "Old fact"), None)
        assert old is not None
        assert abs(old.confidence - 0.4) < 1e-9
