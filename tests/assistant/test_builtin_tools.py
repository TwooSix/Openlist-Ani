"""Tests for built-in tools: SkillTool, SendMessageTool, AgentTool."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from openlist_ani.assistant.core.models import ProviderResponse
from openlist_ani.assistant.tool.builtin.send_message_tool import SendMessageTool
from openlist_ani.assistant.tool.builtin.skill_tool import SkillTool
from openlist_ani.assistant.tool.builtin.agent_tool import AgentTool
from openlist_ani.assistant.tool.registry import ToolRegistry

from .conftest import MockProvider, ReadOnlyTool


class TestSendMessageTool:
    @pytest.mark.asyncio
    async def test_sends_message(self):
        received = []

        async def callback(msg: str) -> None:
            received.append(msg)

        tool = SendMessageTool(callback)
        result = await tool.execute(message="Processing...")

        assert result == "Message sent."
        assert received == ["Processing..."]

    @pytest.mark.asyncio
    async def test_empty_message_error(self):
        tool = SendMessageTool(AsyncMock())
        result = await tool.execute(message="")
        assert "Error" in result

    def test_is_concurrency_safe(self):
        tool = SendMessageTool(AsyncMock())
        assert tool.is_concurrency_safe() is True

    def test_properties(self):
        tool = SendMessageTool(AsyncMock())
        assert tool.name == "send_message"
        assert "message" in tool.parameters["properties"]


class TestSkillTool:
    @pytest.fixture
    def catalog_with_skill(self, tmp_path: Path):
        from openlist_ani.assistant.skill.catalog import SkillCatalog

        # Create a test skill
        skill_dir = tmp_path / "skills" / "test_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test\ndescription: Test skill\n---\n"
        )
        script_dir = skill_dir / "script"
        script_dir.mkdir()
        (script_dir / "default.py").write_text(
            "def run(**kwargs):\n    return 'skill executed'\n"
        )

        catalog = SkillCatalog(tmp_path / "skills")
        catalog.discover()
        return catalog

    @pytest.mark.asyncio
    async def test_execute_skill(self, catalog_with_skill):
        tool = SkillTool(catalog_with_skill)
        result = await tool.execute(skill_name="test", action="default")
        assert result == "skill executed"

    @pytest.mark.asyncio
    async def test_execute_unknown_skill(self, catalog_with_skill):
        tool = SkillTool(catalog_with_skill)
        result = await tool.execute(skill_name="nonexistent")
        assert "error" in result.lower()

    def test_properties(self, catalog_with_skill):
        tool = SkillTool(catalog_with_skill)
        assert tool.name == "skill_tool"
        assert tool.is_concurrency_safe() is False

    def test_concurrency_safe_per_input(self, catalog_with_skill):
        """SkillTool concurrency depends on action: read-only actions are safe."""
        tool = SkillTool(catalog_with_skill)
        # Read-only actions
        assert tool.is_concurrency_safe({"action": "search"}) is True
        assert tool.is_concurrency_safe({"action": "query"}) is True
        assert tool.is_concurrency_safe({"action": "calendar"}) is True
        assert tool.is_concurrency_safe({"action": "subgroups"}) is True
        assert tool.is_concurrency_safe({"action": "default"}) is True
        # Write actions
        assert tool.is_concurrency_safe({"action": "subscribe"}) is False
        assert tool.is_concurrency_safe({"action": "update_collection"}) is False
        # No input → conservative False
        assert tool.is_concurrency_safe(None) is False


class TestAgentTool:
    @pytest.mark.asyncio
    async def test_execute_general_purpose(self):
        provider = MockProvider([
            ProviderResponse(text="Task done."),
        ])
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("helper"))

        tool = AgentTool(provider, registry)
        result = await tool.execute(prompt="Do a task")

        assert result == "Task done."

    @pytest.mark.asyncio
    async def test_execute_explore_agent(self):
        provider = MockProvider([
            ProviderResponse(text="Found 3 files."),
        ])
        registry = ToolRegistry()
        registry.register(ReadOnlyTool("helper"))

        tool = AgentTool(provider, registry)
        result = await tool.execute(prompt="Search for files", agent_type="explore")

        assert result == "Found 3 files."

    @pytest.mark.asyncio
    async def test_execute_unknown_type_falls_back(self):
        provider = MockProvider([
            ProviderResponse(text="General purpose response."),
        ])
        registry = ToolRegistry()

        tool = AgentTool(provider, registry)
        result = await tool.execute(prompt="Do it", agent_type="unknown_type")

        assert result == "General purpose response."

    @pytest.mark.asyncio
    async def test_empty_prompt_error(self):
        provider = MockProvider()
        registry = ToolRegistry()
        tool = AgentTool(provider, registry)

        result = await tool.execute(prompt="")
        assert "Error" in result

    def test_properties(self):
        provider = MockProvider()
        registry = ToolRegistry()
        tool = AgentTool(provider, registry)

        assert tool.name == "agent"
        assert tool.is_concurrency_safe() is False
        assert "prompt" in tool.parameters["properties"]
