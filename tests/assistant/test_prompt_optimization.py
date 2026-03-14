"""Tests for assistant prompt optimization (OPT-3)."""


class TestSystemPromptOptimization:
    def test_skill_discovery_hint_present(self):
        """System prompt should include skill discovery guidance."""
        from openlist_ani.assistant.assistant import _SKILL_DISCOVERY_HINT

        assert "SKILL.md" in _SKILL_DISCOVERY_HINT
        assert "search_files" in _SKILL_DISCOVERY_HINT
        assert "run_command" in _SKILL_DISCOVERY_HINT


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
