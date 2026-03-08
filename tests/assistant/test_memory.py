"""Tests for file-based assistant memory management."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openlist_ani.assistant.memory import AssistantMemoryManager


@pytest.fixture
def temp_memory_dir(tmp_path) -> Path:
    """Provide an isolated file-backed memory directory."""
    return tmp_path / "assistant-memory"


class TestAssistantMemoryManager:
    async def test_remember_turn_persists_recent_history(self, temp_memory_dir):
        manager = AssistantMemoryManager(
            client=None,
            model="gpt-4o",
            recent_message_limit=4,
            base_dir=temp_memory_dir,
        )

        await manager.remember_turn(
            "telegram:1", "Hello", "Hello, I will remember that."
        )

        history = await manager.load_recent_history("telegram:1")
        assert history == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hello, I will remember that."},
        ]

        session_files = list(
            (temp_memory_dir / "telegram%3A1" / "sessions").glob("*.md")
        )
        daily_files = list((temp_memory_dir / "telegram%3A1" / "daily").glob("*.md"))
        assert len(session_files) == 1
        assert len(daily_files) == 1

    async def test_remember_turn_refreshes_long_term_memory(self, temp_memory_dir):
        response_payload = {
            "summary": "The user wants the assistant to remember important context after restarts.",
            "facts": [
                {
                    "content": "The user values durable long-term memory.",
                    "category": "preference",
                    "confidence": 0.9,
                },
                {
                    "content": "The user primarily interacts through Telegram.",
                    "category": "workflow",
                    "confidence": 0.8,
                },
            ],
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

        manager = AssistantMemoryManager(
            client=fake_client,
            model="gpt-4o",
            recent_message_limit=6,
            refresh_interval_messages=6,
            base_dir=temp_memory_dir,
        )

        await manager.remember_turn(
            "telegram:2",
            "Please remember that I prefer to chat through Telegram.",
            "Understood. I will keep that as long-term memory.",
        )

        snapshot = await manager.load_snapshot("telegram:2")
        assert snapshot.summary == response_payload["summary"]
        assert [fact.content for fact in snapshot.facts] == [
            item["content"] for item in response_payload["facts"]
        ]
        assert [fact.category for fact in snapshot.facts] == [
            item["category"] for item in response_payload["facts"]
        ]

        memory_messages = await manager.build_memory_messages(
            "telegram:2",
            "Can you keep using Telegram for updates?",
        )
        assert len(memory_messages) == 1
        assert response_payload["summary"] in memory_messages[0]["content"]
        assert "Telegram" in memory_messages[0]["content"]

        memory_file = temp_memory_dir / "telegram%3A2" / "MEMORY.md"
        index_file = temp_memory_dir / "telegram%3A2" / "sessions" / "INDEX.md"
        assert memory_file.exists()
        assert index_file.exists()
        assert "# Long-Term Memory" in memory_file.read_text(encoding="utf-8")
        assert "summary:" in index_file.read_text(encoding="utf-8")

    async def test_clear_memory_removes_history_and_summary(self, temp_memory_dir):
        manager = AssistantMemoryManager(
            client=None,
            model="gpt-4o",
            recent_message_limit=4,
            base_dir=temp_memory_dir,
        )

        await manager.remember_turn(
            "telegram:3",
            "Please remember this.",
            "Stored in file-based memory.",
        )

        await manager.clear_memory("telegram:3")

        assert await manager.load_recent_history("telegram:3") == []
        snapshot = await manager.load_snapshot("telegram:3")
        assert snapshot.summary == ""
        assert snapshot.facts == []
        assert not (temp_memory_dir / "telegram%3A3").exists()

    def test_get_user_dir_rejects_base_dir_escape(self, temp_memory_dir):
        manager = AssistantMemoryManager(
            client=None,
            model="gpt-4o",
            recent_message_limit=4,
            base_dir=temp_memory_dir,
        )

        with pytest.raises(ValueError, match="Invalid memory key"):
            manager._get_user_dir("..")

        with pytest.raises(ValueError, match="Invalid memory key"):
            manager._get_user_dir("telegram:bad/key")
