"""Tests for MetadataFilter."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openlist_ani.core.rss.filter.metadata import MetadataFilter
from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)

# ── helpers ──────────────────────────────────────────────────────────

_CFG = "openlist_ani.core.rss.filter.metadata.config"


def _make_resource(
    title: str = "Test Anime - 01",
    fansub: str | None = None,
    quality: VideoQuality | None = VideoQuality.Q1080P,
    languages: list[LanguageType] | None = None,
) -> AnimeResourceInfo:
    return AnimeResourceInfo(
        title=title,
        download_url="magnet:?xt=urn:btih:abc",
        fansub=fansub,
        quality=quality,
        languages=languages or [],
    )


def _mock_config(
    exclude_fansub: list[str] | None = None,
    exclude_quality: list[str] | None = None,
    exclude_languages: list[str] | None = None,
):
    """Return a mock config with the given filter settings."""
    filter_cfg = SimpleNamespace(
        exclude_fansub=exclude_fansub or [],
        exclude_quality=exclude_quality or [],
        exclude_languages=exclude_languages or [],
    )
    rss = SimpleNamespace(filter=filter_cfg)
    return SimpleNamespace(rss=rss)


# ── tests ────────────────────────────────────────────────────────────


class TestMetadataFilter:
    @pytest.mark.asyncio
    async def test_empty_config_passes_all(self):
        """No exclusion rules → everything passes."""
        f = MetadataFilter()
        candidates = [_make_resource("A"), _make_resource("B")]
        with patch(_CFG, _mock_config()):
            result = f.apply(candidates)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        """Empty batch returns empty list."""
        f = MetadataFilter()
        result = f.apply([])
        assert result == []

    @pytest.mark.asyncio
    async def test_exclude_fansub(self):
        """Entries with excluded fansub should be filtered out."""
        f = MetadataFilter()
        candidates = [
            _make_resource("A", fansub="BadSub"),
            _make_resource("B", fansub="GoodSub"),
            _make_resource("C", fansub=None),
        ]
        with patch(_CFG, _mock_config(exclude_fansub=["BadSub"])):
            result = f.apply(candidates)
        assert len(result) == 2
        fansubs = {r.fansub for r in result}
        assert "BadSub" not in fansubs

    @pytest.mark.asyncio
    async def test_exclude_multiple_fansubs(self):
        """Multiple fansubs in exclusion list."""
        f = MetadataFilter()
        candidates = [
            _make_resource("A", fansub="Sub_A"),
            _make_resource("B", fansub="Sub_B"),
            _make_resource("C", fansub="Sub_C"),
        ]
        with patch(_CFG, _mock_config(exclude_fansub=["Sub_A", "Sub_B"])):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].fansub == "Sub_C"

    @pytest.mark.asyncio
    async def test_exclude_quality(self):
        """Entries with excluded quality should be filtered out."""
        f = MetadataFilter()
        candidates = [
            _make_resource("A", quality=VideoQuality.Q480P),
            _make_resource("B", quality=VideoQuality.Q1080P),
        ]
        with patch(_CFG, _mock_config(exclude_quality=["480p"])):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].quality == VideoQuality.Q1080P

    @pytest.mark.asyncio
    async def test_exclude_quality_unknown(self):
        """Entries with UNKNOWN quality should be excludable."""
        f = MetadataFilter()
        candidates = [
            _make_resource("A", quality=VideoQuality.UNKNOWN),
            _make_resource("B", quality=VideoQuality.Q1080P),
        ]
        with patch(_CFG, _mock_config(exclude_quality=["unknown"])):
            result = f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exclude_quality_none_not_excluded(self):
        """Entry with quality=None should not be excluded by quality filter."""
        f = MetadataFilter()
        candidates = [_make_resource("A", quality=None)]
        with patch(_CFG, _mock_config(exclude_quality=["480p"])):
            result = f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exclude_languages(self):
        """Entries with any excluded language should be filtered out."""
        f = MetadataFilter()
        candidates = [
            _make_resource(
                "A", languages=[LanguageType.CHS, LanguageType.CHT]
            ),
            _make_resource("B", languages=[LanguageType.JP]),
            _make_resource("C", languages=[LanguageType.UNKNOWN]),
        ]
        with patch(_CFG, _mock_config(exclude_languages=["未知"])):
            result = f.apply(candidates)
        assert len(result) == 2
        titles = {r.title for r in result}
        assert "C" not in titles

    @pytest.mark.asyncio
    async def test_exclude_language_any_match(self):
        """If any of candidate's languages matches exclusion, it's excluded."""
        f = MetadataFilter()
        candidates = [
            _make_resource(
                "A", languages=[LanguageType.CHS, LanguageType.UNKNOWN]
            ),
        ]
        with patch(_CFG, _mock_config(exclude_languages=["未知"])):
            result = f.apply(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_combined_exclusion_rules(self):
        """Multiple exclusion types can work together."""
        f = MetadataFilter()
        candidates = [
            _make_resource("A", fansub="BadSub", quality=VideoQuality.Q1080P),
            _make_resource("B", fansub="GoodSub", quality=VideoQuality.Q480P),
            _make_resource(
                "C",
                fansub="GoodSub",
                quality=VideoQuality.Q1080P,
                languages=[LanguageType.CHS],
            ),
        ]
        with patch(
            _CFG,
            _mock_config(
                exclude_fansub=["BadSub"],
                exclude_quality=["480p"],
            ),
        ):
            result = f.apply(candidates)
        assert len(result) == 1
        assert result[0].title == "C"

    @pytest.mark.asyncio
    async def test_empty_languages_not_excluded(self):
        """Entry with no languages should not be excluded by language filter."""
        f = MetadataFilter()
        candidates = [_make_resource("A", languages=[])]
        with patch(_CFG, _mock_config(exclude_languages=["未知"])):
            result = f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_fansub_not_excluded(self):
        """Entry with fansub=None should not match a string exclusion."""
        f = MetadataFilter()
        candidates = [_make_resource("A", fansub=None)]
        with patch(_CFG, _mock_config(exclude_fansub=["SomeSub"])):
            result = f.apply(candidates)
        assert len(result) == 1
