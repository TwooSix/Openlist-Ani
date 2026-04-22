"""Tests for RegexTitleFilter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openlist_ani.core.rss.filter.regex import RegexTitleFilter
from openlist_ani.core.website.model import AnimeResourceInfo

# ── helpers ──────────────────────────────────────────────────────────

_CFG = "openlist_ani.core.rss.filter.regex.config"


def _make_resource(title: str = "Test Anime - 01") -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url="magnet:?xt=urn:btih:abc")


def _mock_config(exclude_patterns: list[str] | None = None):
    """Return a mock config with the given exclude_patterns."""
    filter_cfg = SimpleNamespace(exclude_patterns=exclude_patterns or [])
    rss = SimpleNamespace(filter=filter_cfg)
    return SimpleNamespace(rss=rss)


# ── tests ────────────────────────────────────────────────────────────


class TestRegexTitleFilter:
    @pytest.mark.asyncio
    async def test_default_pattern_excludes_collections(self):
        """Default pattern should exclude collection-style titles."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource(
                "[7³ACG] 岁月流逝饭菜依旧美味/Hibi wa Sugiredo Meshi Umashi S01 | 01-12 [简繁字幕] BDrip 1080p x265 OPUS 2.0"
            ),
            _make_resource(
                "[SweetSub][正相反的你与我][Seihantai na Kimi to Boku][01-12 精校合集][WebRip][1080P][AVC 8bit][简日双语]"
            ),
            _make_resource(
                "[奶活家教压制组][东京喰种√A/东京喰种][Tokyo Ghoul][S01][TV01-12Fin][BDRip][1080p][FLAC][X265]"
            ),
            _make_resource(
                "[云光字幕组]葬送的芙莉莲 第二季 Sousou no Frieren S2 [合集][简体双语][1080p]招募翻译V2"
            ),
            _make_resource("[字幕组] 动漫02 - 01"),
        ]
        with patch(_CFG, _mock_config([])):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].title == "[字幕组] 动漫02 - 01"

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        """Empty batch returns empty list."""
        f = RegexTitleFilter()
        result = f.apply([])
        assert result == []

    @pytest.mark.asyncio
    async def test_matching_pattern_excludes(self):
        """Entry matching a pattern should be excluded."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource("[字幕组] 动漫 合集 01-12"),
            _make_resource("[字幕组] 动漫 - 01"),
        ]
        with patch(_CFG, _mock_config(["合集"])):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].title == "[字幕组] 动漫 - 01"

    @pytest.mark.asyncio
    async def test_multiple_patterns(self):
        """Multiple patterns: any match excludes the entry."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource("Anime - SP01"),
            _make_resource("Anime - 01 HEVC"),
            _make_resource("Anime - 01"),
        ]
        with patch(_CFG, _mock_config(["SP\\d+", "HEVC"])):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].title == "Anime - 01"

    @pytest.mark.asyncio
    async def test_regex_search_not_fullmatch(self):
        """Pattern uses search (partial match), not fullmatch."""
        f = RegexTitleFilter()
        candidates = [_make_resource("Anime 720p x264 - 01")]
        with patch(_CFG, _mock_config(["720p"])):
            result = f.apply(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_match_passes_through(self):
        """Entries not matching any pattern should pass."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource("Good Anime - 01"),
            _make_resource("Good Anime - 02"),
        ]
        with patch(_CFG, _mock_config(["合集", "SP\\d+"])):
            result = f.apply(candidates)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_case_sensitive_by_default(self):
        """Regex matching should be case-sensitive by default."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource("Anime HEVC - 01"),
            _make_resource("Anime hevc - 01"),
        ]
        with patch(_CFG, _mock_config(["HEVC"])):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].title == "Anime hevc - 01"

    @pytest.mark.asyncio
    async def test_case_insensitive_with_flag(self):
        """User can use (?i) in pattern for case-insensitive matching."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource("Anime HEVC - 01"),
            _make_resource("Anime hevc - 01"),
        ]
        with patch(_CFG, _mock_config(["(?i)hevc"])):
            result = f.apply(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_complex_regex(self):
        """Complex regex patterns should work."""
        f = RegexTitleFilter()
        candidates = [
            _make_resource("[Fansub] Anime - 01 [1080p]"),
            _make_resource("[Fansub] Anime - 01 [480p]"),
            _make_resource("[Fansub] Anime - 01 [720p]"),
        ]
        with patch(_CFG, _mock_config(["\\[480p\\]"])):
            result = f.apply(candidates)
        assert len(result) == 2
        titles = {r.title for r in result}
        assert "[Fansub] Anime - 01 [480p]" not in titles
