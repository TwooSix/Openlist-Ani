"""Tests for context builder -- directory-based memory + CLAUDE.md architecture."""

import pytest
from pathlib import Path

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.models import Role
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.skill.catalog import SkillCatalog


@pytest.fixture
def memory_with_data(tmp_path: Path):
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()

    # Project CLAUDE.md
    (project / "CLAUDE.md").write_text("Use pytest for testing.\n")

    mm = MemoryManager(data_dir=data_dir, project_root=project)
    return mm


@pytest.fixture
def empty_memory(tmp_path: Path):
    data_dir = tmp_path / "empty_data"
    project = tmp_path / "empty_project"
    project.mkdir()
    return MemoryManager(data_dir=data_dir, project_root=project)


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_build_includes_system_prompt(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hello!")

        system_msg = messages[0]
        assert system_msg.role == Role.SYSTEM
        # Should contain the SOUL.md content (identity intro)
        assert "Openlist-Ani Assistant" in system_msg.content
        assert "anime tracking" in system_msg.content

    @pytest.mark.asyncio
    async def test_build_includes_tool_instructions(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "# How to Use Tools" in system_content
        assert "available_skills" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_tool_usage(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "# How to Use Tools" in system_content
        assert "immediately call the appropriate tool" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_behavioral_rules(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "# Behavioral Rules" in system_content
        assert "ALWAYS use tools first" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_match_skill_to_intent(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "Match skill to intent" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_tone_section(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "# Tone and Style" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_conciseness_rules(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "Be concise and direct" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_claude_md(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        # Should include CLAUDE.md content from project root
        assert "pytest for testing" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_memory_prompt(self, memory_with_data):
        # Write a memory entry to the memory/ dir MEMORY.md index
        memory_dir = memory_with_data.memory_dir
        (memory_dir.path / "MEMORY.md").write_text(
            "- [Prefs](prefs.md) \u2014 user preferences\n"
        )

        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "# Memory" in system_content
        assert "Prefs" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_memory_instructions(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hi")

        system_content = messages[0].content
        # Should always include memory system instructions
        assert "# Memory" in system_content
        assert "Memory types" in system_content

    @pytest.mark.asyncio
    async def test_build_includes_environment(self, memory_with_data):
        builder = ContextBuilder(
            memory_with_data,
            model_name="gpt-4o",
            provider_type="openai",
        )
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "# Environment" in system_content
        assert "gpt-4o" in system_content
        assert "openai" in system_content

    @pytest.mark.asyncio
    async def test_build_ends_with_user_message(self, memory_with_data):
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("What is 2+2?")

        last_msg = messages[-1]
        assert last_msg.role == Role.USER
        assert last_msg.content == "What is 2+2?"

    @pytest.mark.asyncio
    async def test_build_with_empty_memory(self, empty_memory):
        builder = ContextBuilder(empty_memory)
        messages = builder.build("Hello!")

        # Should still have system message
        assert any(m.role == Role.SYSTEM for m in messages)
        # Should end with user message
        assert messages[-1].role == Role.USER
        assert messages[-1].content == "Hello!"
        # System prompt should still have SOUL.md sections
        system_content = messages[0].content
        assert "# How to Use Tools" in system_content
        assert "# Behavioral Rules" in system_content

    @pytest.mark.asyncio
    async def test_build_with_skill_catalog(self, memory_with_data, tmp_path):
        # Create a skill
        skills_dir = tmp_path / "skills" / "test_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: test\ndescription: A test skill\n---\n"
        )

        catalog = SkillCatalog(tmp_path / "skills")
        catalog.discover()

        builder = ContextBuilder(memory_with_data, catalog)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "available_skills" in system_content
        assert "test" in system_content

    @pytest.mark.asyncio
    async def test_build_message_count(self, memory_with_data):
        """Should have exactly 2 messages: system + user."""
        builder = ContextBuilder(memory_with_data)
        messages = builder.build("Hello!")

        assert len(messages) == 2
        assert messages[0].role == Role.SYSTEM
        assert messages[1].role == Role.USER

    @pytest.mark.asyncio
    async def test_build_empty_memory_shows_no_memories(self, empty_memory):
        """Should show 'No memories stored yet' when memory index is empty."""
        builder = ContextBuilder(empty_memory)
        messages = builder.build("Hi")

        system_content = messages[0].content
        assert "No memories stored yet" in system_content
