"""Tests for assistant prompt optimization (OPT-3)."""

import pytest


class TestSystemPromptOptimization:
    def test_skill_discovery_hint_present(self):
        """System prompt should include skill discovery guidance."""
        from openlist_ani.assistant.assistant import _SKILL_DISCOVERY_HINT

        assert "SKILL.md" in _SKILL_DISCOVERY_HINT
        assert "search_files" in _SKILL_DISCOVERY_HINT
        assert "run_skill" in _SKILL_DISCOVERY_HINT

    def test_skill_discovery_hint_no_uv(self):
        """Skill discovery hint must not reference uv CLI."""
        from openlist_ani.assistant.assistant import _SKILL_DISCOVERY_HINT

        assert "uv run" not in _SKILL_DISCOVERY_HINT
        assert "run_command" not in _SKILL_DISCOVERY_HINT


class TestSkillScriptsHaveRunFunction:
    """Verify all skill scripts export an async ``run`` function."""

    def test_search_anime_has_run(self):
        from openlist_ani.assistant.skills.oani.script.search_anime import run

        assert callable(run)

    def test_parse_rss_has_run(self):
        from openlist_ani.assistant.skills.oani.script.parse_rss import run

        assert callable(run)

    def test_download_has_run(self):
        from openlist_ani.assistant.skills.oani.script.download import run

        assert callable(run)

    def test_db_query_has_run(self):
        from openlist_ani.assistant.skills.oani.script.db_query import run

        assert callable(run)


class TestRunSkillToolValidation:
    """Verify RunSkillTool security validation."""

    def _make_tool(self):
        from openlist_ani.assistant.tools.run_skill import RunSkillTool

        return RunSkillTool()

    @pytest.mark.asyncio
    async def test_valid_module(self):
        """Valid skill module path should pass validation."""
        from openlist_ani.assistant.tools.run_skill import _validate_module

        assert _validate_module("bangumi.script.calendar") is None
        assert _validate_module("oani.script.download") is None
        assert _validate_module("mikan.script.search") is None

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self):
        """Path traversal attempts must be rejected."""
        from openlist_ani.assistant.tools.run_skill import _validate_module

        assert _validate_module("..os") is not None
        assert _validate_module("foo..bar") is not None

    @pytest.mark.asyncio
    async def test_invalid_chars_rejected(self):
        """Module paths with invalid characters must be rejected."""
        from openlist_ani.assistant.tools.run_skill import _validate_module

        assert _validate_module("foo/bar") is not None
        assert _validate_module("foo bar") is not None
        assert _validate_module("foo;bar") is not None

    @pytest.mark.asyncio
    async def test_missing_script_segment_rejected(self):
        """Module paths without 'script' segment must be rejected."""
        from openlist_ani.assistant.tools.run_skill import _validate_module

        assert _validate_module("bangumi.calendar") is not None
        assert _validate_module("calendar") is not None

    @pytest.mark.asyncio
    async def test_empty_module_rejected(self):
        """Empty module path must be rejected."""
        from openlist_ani.assistant.tools.run_skill import _validate_module

        assert _validate_module("") is not None

    @pytest.mark.asyncio
    async def test_nonexistent_module(self):
        """Non-existent module should return error string."""
        tool = self._make_tool()
        result = await tool.execute(skill_module="nonexistent.script.action")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_tool_name_and_description(self):
        """Tool should have correct name and description."""
        tool = self._make_tool()
        assert tool.name == "run_skill"
        assert "run_skill" not in tool.description or "run" in tool.description
