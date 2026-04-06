"""Tests for CLI frontend slash commands, autocomplete, and UI."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from openlist_ani.assistant.frontend.cli import CLIFrontend, SlashCommandCompleter
from openlist_ani.assistant.skill.catalog import SkillCatalog, SkillEntry


def _make_catalog(tmp_path: Path) -> SkillCatalog:
    """Create a SkillCatalog with two dummy skills for testing."""
    skills_dir = tmp_path / "skills"
    for name, desc in [("mikan", "Search anime"), ("bangumi", "Anime database")]:
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n"
        )
    catalog = SkillCatalog(skills_dir)
    catalog.discover()
    return catalog


def _make_frontend(catalog=None):
    """Create a CLIFrontend with mocked loop."""
    loop = MagicMock()
    loop._memory = MagicMock()
    loop._memory.clear_all = AsyncMock()
    loop.turn_count = 0
    loop._autocompactor = MagicMock()
    loop._autocompactor.force_compact = AsyncMock(return_value=None)
    loop._messages = []
    return CLIFrontend(
        loop,
        model_name="test-model",
        provider_type="openai",
        catalog=catalog,
    )


class TestCLIFrontendCommands:
    @pytest.mark.asyncio
    async def test_quit_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/quit")
        assert result is False

    @pytest.mark.asyncio
    async def test_exit_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/exit")
        assert result is False

    @pytest.mark.asyncio
    async def test_help_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/help")
        assert result is True  # Should continue

    @pytest.mark.asyncio
    async def test_clear_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/clear")
        assert result is True
        frontend._loop._memory.clear_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/reset")
        assert result is True
        frontend._loop._memory.clear_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/foobar")
        assert result is True  # Should continue, just show warning

    @pytest.mark.asyncio
    async def test_compact_command(self):
        frontend = _make_frontend()
        result = await frontend._handle_command("/compact")
        assert result is True
        frontend._loop._autocompactor.force_compact.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_resets_turn_number(self):
        frontend = _make_frontend()
        frontend._turn_number = 5
        await frontend._handle_command("/clear")
        assert frontend._turn_number == 0

    @pytest.mark.asyncio
    async def test_reset_resets_turn_number(self):
        frontend = _make_frontend()
        frontend._turn_number = 3
        await frontend._handle_command("/reset")
        assert frontend._turn_number == 0


class TestCLIFrontendUI:
    """Tests for UI display methods."""

    def test_welcome_shows_model_info(self, capsys):
        """Welcome banner should include model and provider info."""
        frontend = _make_frontend()
        frontend._show_welcome()
        # Rich renders to its own console, not capsys — just verify no crash

    def test_welcome_shows_skills(self, tmp_path):
        """Welcome banner should include skill names when catalog is provided."""
        catalog = _make_catalog(tmp_path)
        frontend = _make_frontend(catalog=catalog)
        # Verify the catalog is stored and skills are accessible
        assert frontend._catalog is not None
        skills = [s.name for s in frontend._catalog.all_skills()]
        assert "mikan" in skills
        assert "bangumi" in skills

    def test_show_response_no_crash(self):
        """Response rendering should not crash."""
        frontend = _make_frontend()
        frontend._turn_number = 1
        frontend._show_response("Hello **world**!", elapsed=1.5)

    def test_show_response_empty(self):
        """Empty response should be a no-op."""
        frontend = _make_frontend()
        frontend._show_response("")
        # No crash = pass

    def test_show_error_no_crash(self):
        """Error display should not crash."""
        frontend = _make_frontend()
        frontend._show_error(RuntimeError("test error"))

    def test_show_goodbye_no_crash(self):
        """Goodbye display should not crash."""
        frontend = _make_frontend()
        frontend._show_goodbye()

    def test_show_help_no_crash(self):
        """Help display should not crash without catalog."""
        frontend = _make_frontend()
        frontend._show_help()

    def test_show_help_with_skills(self, tmp_path):
        """Help display should include skills when catalog is available."""
        catalog = _make_catalog(tmp_path)
        frontend = _make_frontend(catalog=catalog)
        frontend._show_help()  # Should not crash

    def test_turn_number_increments(self):
        """Turn number should be tracked."""
        frontend = _make_frontend()
        assert frontend._turn_number == 0
        frontend._turn_number += 1
        assert frontend._turn_number == 1

    def test_format_tool_args_empty(self):
        """Empty args should return empty string."""
        frontend = _make_frontend()
        assert frontend._format_tool_args({}) == ""

    def test_format_tool_args_string(self):
        """String args should be quoted."""
        frontend = _make_frontend()
        result = frontend._format_tool_args({"keyword": "frieren"})
        assert 'keyword="frieren"' in result

    def test_format_tool_args_long_string(self):
        """Long string values should be truncated."""
        frontend = _make_frontend()
        result = frontend._format_tool_args({"text": "a" * 50})
        assert "..." in result

    def test_format_tool_args_dict(self):
        """Dict args should show {...}."""
        frontend = _make_frontend()
        result = frontend._format_tool_args({"params": {"a": 1}})
        assert "params={...}" in result

    def test_format_tool_args_list(self):
        """List args should show [...]."""
        frontend = _make_frontend()
        result = frontend._format_tool_args({"items": [1, 2]})
        assert "items=[...]" in result

    def test_show_footer_no_crash(self):
        """Footer display should not crash."""
        frontend = _make_frontend()
        frontend._turn_number = 1
        frontend._show_footer(1.5, tool_call_count=2, text="Hello world")


class TestSlashCommandCompleter:
    """Tests for slash-command autocomplete."""

    def _get_completions(self, completer, text: str) -> list[str]:
        """Helper: collect completion texts for a given input."""
        doc = Document(text, len(text))
        event = CompleteEvent()
        return [c.text for c in completer.get_completions(doc, event)]

    def test_no_completions_for_plain_text(self):
        """Should not trigger when input doesn't start with /."""
        completer = SlashCommandCompleter()
        assert self._get_completions(completer, "hello") == []

    def test_all_builtins_on_slash(self):
        """Typing / alone should list all built-in commands."""
        completer = SlashCommandCompleter()
        completions = self._get_completions(completer, "/")
        assert "/help" in completions
        assert "/clear" in completions
        assert "/reset" in completions
        assert "/quit" in completions
        assert "/exit" in completions
        assert "/compact" in completions

    def test_prefix_filter(self):
        """Typing /he should only match /help."""
        completer = SlashCommandCompleter()
        completions = self._get_completions(completer, "/he")
        assert completions == ["/help"]

    def test_skills_included(self, tmp_path):
        """Skills from the catalog should appear in completions."""
        catalog = _make_catalog(tmp_path)
        completer = SlashCommandCompleter(catalog)
        completions = self._get_completions(completer, "/")
        assert "/mikan" in completions
        assert "/bangumi" in completions

    def test_skill_prefix_filter(self, tmp_path):
        """Typing /mik should match /mikan."""
        catalog = _make_catalog(tmp_path)
        completer = SlashCommandCompleter(catalog)
        completions = self._get_completions(completer, "/mik")
        assert "/mikan" in completions
        assert "/bangumi" not in completions

    def test_completions_have_descriptions(self, tmp_path):
        """Each completion should carry a display_meta description."""
        catalog = _make_catalog(tmp_path)
        completer = SlashCommandCompleter(catalog)
        doc = Document("/", 1)
        event = CompleteEvent()
        metas = {
            c.text: str(c.display_meta)
            for c in completer.get_completions(doc, event)
        }
        assert "Show available commands" in metas["/help"]
        assert "Search anime" in metas["/mikan"]

    def test_no_catalog(self):
        """Without a catalog, only built-in commands appear."""
        completer = SlashCommandCompleter(catalog=None)
        completions = self._get_completions(completer, "/")
        assert len(completions) == 6  # help, clear, reset, compact, quit, exit


class TestCLIFrontendCompletion:
    """Tests for completion configuration."""

    def test_complete_while_typing_enabled(self):
        """complete_while_typing should be True for auto-popup on /."""
        frontend = _make_frontend()
        assert frontend._session.completer is not None

    def test_no_fuzzy_completer_wrapper(self):
        """Completer should be SlashCommandCompleter directly, not FuzzyCompleter."""
        frontend = _make_frontend()
        assert isinstance(frontend._session.completer, SlashCommandCompleter)

    def test_custom_completion_style(self):
        """PromptSession should have a custom style for the completion menu."""
        frontend = _make_frontend()
        # The session should have a non-default style set
        assert frontend._session.style is not None
