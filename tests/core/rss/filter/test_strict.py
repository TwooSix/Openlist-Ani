"""Tests for StrictRenameFilter and compute_rename_stem."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.rss.filter.strict import (
    StrictRenameFilter,
    compute_rename_stem,
)
from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)

# ── helpers ──────────────────────────────────────────────────────────

_DEFAULT_FMT = (
    "{anime_name} S{season:02d}E{episode:02d} {fansub} {quality} {languages}"
)

_DB_FIND = "openlist_ani.core.rss.filter.strict.db.find_resources_by_episode"
_CFG = "openlist_ani.core.rss.filter.strict.config"


def _make_resource(
    title: str = "resource",
    url: str = "magnet:?xt=urn:btih:abc",
    anime_name: str = "TestAnime",
    season: int = 1,
    episode: int = 1,
    fansub: str | None = None,
    quality: VideoQuality = VideoQuality.Q1080P,
    languages: list[LanguageType] | None = None,
    version: int = 1,
) -> AnimeResourceInfo:
    return AnimeResourceInfo(
        title=title,
        download_url=url,
        anime_name=anime_name,
        season=season,
        episode=episode,
        fansub=fansub,
        quality=quality,
        languages=languages or [],
        version=version,
    )


def _make_db_record(
    fansub: str | None = None,
    quality: str | None = "1080p",
    languages: str = "",
    version: int = 1,
) -> dict:
    return {
        "fansub": fansub,
        "quality": quality,
        "languages": languages,
        "version": version,
    }


def _mock_config(rename_format: str = _DEFAULT_FMT):
    """Return a mock config with the given rename_format."""
    from types import SimpleNamespace

    openlist = SimpleNamespace(rename_format=rename_format)
    return SimpleNamespace(openlist=openlist)


# ── compute_rename_stem unit tests ──────────────────────────────────


class TestComputeRenameStem:
    def test_basic_format(self):
        stem = compute_rename_stem(
            _DEFAULT_FMT,
            anime_name="进击的巨人",
            season=1,
            episode=5,
            fansub="Fansub_A",
            quality="1080p",
            languages="简繁",
        )
        assert stem == "进击的巨人 S01E05 Fansub_A 1080p 简繁"

    def test_none_values_become_empty(self):
        stem = compute_rename_stem(
            _DEFAULT_FMT,
            anime_name="Test",
            season=1,
            episode=1,
            fansub=None,
            quality=None,
            languages="",
        )
        assert "None" not in stem
        assert "Test S01E01" in stem

    def test_format_error_fallback(self):
        """Invalid format string should fallback gracefully."""
        stem = compute_rename_stem(
            "{anime_name} {nonexistent_field}",
            anime_name="Test",
            season=1,
            episode=2,
        )
        assert stem == "Test S01E02"

    def test_strip_whitespace(self):
        stem = compute_rename_stem(
            "{anime_name} {fansub}",
            anime_name="Test",
            season=1,
            episode=1,
            fansub=None,
        )
        # Should be stripped, not "Test "
        assert stem == "Test"

    def test_custom_format(self):
        stem = compute_rename_stem(
            "{anime_name} E{episode:02d}",
            anime_name="Anime",
            season=1,
            episode=3,
        )
        assert stem == "Anime E03"


# ── StrictRenameFilter tests ────────────────────────────────────────


class TestStrictRenameFilter:
    @pytest.mark.asyncio
    async def test_matching_stem_filtered(self):
        """Entry whose stem matches a DB record should be filtered out."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="entry-1",
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
            ),
        ]
        db_records = [_make_db_record(fansub="Fansub_A", quality="1080p")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_matching_stem_passes(self):
        """Entry whose stem differs from DB records should pass."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="entry-1",
                fansub="Fansub_B",
                quality=VideoQuality.Q720P,
            ),
        ]
        db_records = [_make_db_record(fansub="Fansub_A", quality="1080p")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_version_upgrade_bypass(self):
        """Higher version should bypass strict filter even with matching stem."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="entry-v2",
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                version=2,
            ),
        ]
        db_records = [
            _make_db_record(fansub="Fansub_A", quality="1080p", version=1)
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_same_version_blocked(self):
        """Same version with matching stem should be blocked."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="entry-dup",
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                version=1,
            ),
        ]
        db_records = [
            _make_db_record(fansub="Fansub_A", quality="1080p", version=1)
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_metadata_bypasses(self):
        """Entries without anime_name/season/episode bypass strict filter."""
        f = StrictRenameFilter()
        candidates = [_make_resource(title="no-meta")]
        candidates[0].anime_name = None

        with patch(_CFG, _mock_config()):
            result = await f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_db_records_passes(self):
        """If no existing downloads for the episode, entry passes."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(title="new-entry", fansub="Fansub_A"),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        """Empty batch returns empty list."""
        f = StrictRenameFilter()
        result = await f.apply([])
        assert result == []

    @pytest.mark.asyncio
    async def test_intra_batch_dedup_keeps_highest_version(self):
        """Within a batch, same stem keeps only the highest version."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="entry-v1",
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                version=1,
                url="magnet:v1",
            ),
            _make_resource(
                title="entry-v2",
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                version=2,
                url="magnet:v2",
            ),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert len(result) == 1
        assert result[0].title == "entry-v2"

    @pytest.mark.asyncio
    async def test_different_stems_both_pass(self):
        """Entries with different stems should both pass."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="entry-a",
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                url="magnet:a",
            ),
            _make_resource(
                title="entry-b",
                fansub="Fansub_B",
                quality=VideoQuality.Q720P,
                url="magnet:b",
            ),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_different_episodes_independent(self):
        """Strict filter is per-episode; different episodes are independent."""
        f = StrictRenameFilter()
        candidates = [
            _make_resource(
                title="ep1",
                episode=1,
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                url="magnet:e1",
            ),
            _make_resource(
                title="ep2",
                episode=2,
                fansub="Fansub_A",
                quality=VideoQuality.Q1080P,
                url="magnet:e2",
            ),
        ]

        _mock_find = AsyncMock(
            side_effect=lambda anime_name, season, episode: (
                [_make_db_record(fansub="Fansub_A", quality="1080p")]
                if episode == 1
                else []
            )
        )

        with (
            patch(_DB_FIND, new=_mock_find),
            patch(_CFG, _mock_config()),
        ):
            result = await f.apply(candidates)
        # Episode 1: stem matches DB → filtered
        # Episode 2: no DB records → passes
        assert len(result) == 1
        assert result[0].episode == 2
