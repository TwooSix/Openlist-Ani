"""Tests for assistant prompt optimization (OPT-3)."""

from openlist_ani.assistant.assistant import AniAssistant
from openlist_ani.assistant.tools.db_tool import ExecuteSqlTool
from openlist_ani.assistant.tools.download_tool import DownloadResourceTool
from openlist_ani.assistant.tools.parse_rss import ParseRssTool
from openlist_ani.assistant.tools.search_anime import SearchAnimeTool


class TestSystemPromptOptimization:

    def test_system_prompt_is_compact(self):
        """System prompt should be significantly shorter than the old ~1200 token version."""
        prompt = AniAssistant.DEFAULT_SYSTEM_PROMPT
        word_count = len(prompt.split())
        assert word_count < 200, f"System prompt too verbose: {word_count} words"

    def test_system_prompt_contains_key_rules(self):
        prompt = AniAssistant.DEFAULT_SYSTEM_PROMPT
        assert "NEVER download" in prompt
        assert "resources" in prompt
        assert "Pagination" in prompt or "pagination" in prompt
        assert "LIMIT" in prompt

    def test_system_prompt_contains_schema(self):
        prompt = AniAssistant.DEFAULT_SYSTEM_PROMPT
        assert "anime_name" in prompt
        assert "downloaded_at" in prompt


class TestToolDescriptions:

    def test_search_tool_has_scenario_guidance(self):
        tool = SearchAnimeTool()
        desc = tool.description
        assert "mikan" in desc or "dmhy" in desc
        assert "download" in desc.lower()

    def test_parse_rss_tool_has_scenario_guidance(self):
        tool = ParseRssTool()
        desc = tool.description
        assert "RSS" in desc
        assert "download_resource" in desc

    def test_download_tool_has_scenario_guidance(self):
        tool = DownloadResourceTool()
        desc = tool.description
        assert "magnet" in desc or "torrent" in desc
        assert "NEVER" in desc or "already" in desc

    def test_db_tool_has_scenario_guidance(self):
        tool = ExecuteSqlTool()
        desc = tool.description
        assert "SELECT" in desc
        assert "LIMIT" in desc or "pagination" in desc.lower()
        assert "resources" in desc
