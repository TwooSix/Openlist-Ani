"""Tests for the resource priority filtering system."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.rss.priority import (
    ResourcePriorityFilter,
    _index_or_none,
    _level_sort_key,
)
from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)

# ── helpers ──────────────────────────────────────────────────────────


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


# Patch targets
_DB_FIND = "openlist_ani.core.rss.priority.db.find_resources_by_episode"
_CFG_PRIORITY = "openlist_ani.core.rss.priority.config"


def _mock_config(
    fansub: list[str] | None = None,
    languages: list[str] | None = None,
    quality: list[str] | None = None,
    field_order: list[str] | None = None,
):
    """Return a mock config whose .rss.priority attributes give the supplied lists."""
    from types import SimpleNamespace

    priority = SimpleNamespace(
        fansub=fansub or [],
        languages=languages or [],
        quality=quality if quality is not None else ["2160p", "1080p", "720p", "480p"],
        field_order=field_order or ["fansub", "quality", "languages"],
    )
    rss = SimpleNamespace(priority=priority)
    cfg = SimpleNamespace(rss=rss)
    return cfg


# ── unit tests for module-level helpers ──────────────────────────────


class TestIndexOrNone:
    def test_found(self):
        assert _index_or_none("b", ["a", "b", "c"]) == 1

    def test_not_found(self):
        assert _index_or_none("x", ["a", "b"]) is None

    def test_empty_list(self):
        assert _index_or_none("a", []) is None


class TestLevelSortKey:
    def test_none_becomes_inf(self):
        assert _level_sort_key((None, 0)) == (float("inf"), 0)

    def test_all_none(self):
        assert _level_sort_key((None, None)) == (float("inf"), float("inf"))

    def test_all_ranked(self):
        assert _level_sort_key((1, 2)) == (1, 2)

    def test_ordering(self):
        assert _level_sort_key((0, 1)) < _level_sort_key((0, 2))
        assert _level_sort_key((0, 1)) < _level_sort_key((None, 0))


# ── fansub priority tests ───────────────────────────────────────────


class TestFansubPriority:
    """Fansub group priority filtering."""

    @pytest.mark.asyncio
    async def test_skip_lower_priority_fansub(self):
        """If highest-priority fansub is already downloaded, skip lower ones."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-other", fansub="Fansub_C"),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_B", "Fansub_C"])),
        ):
            result = await f.filter_batch(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_allow_higher_priority_fansub(self):
        """Higher-priority fansub should still be downloaded."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-ani", fansub="Fansub_B"),
        ]
        downloaded = [_make_db_record(fansub="Fansub_C")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_B", "Fansub_C"])),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 1
        assert result[0].fansub == "Fansub_B"

    @pytest.mark.asyncio
    async def test_multi_level_fansub_priority(self):
        """With [Fansub_A, Fansub_B], if Fansub_B downloaded, skip all except Fansub_A."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-mmnt", fansub="Fansub_A"),
            _make_resource(title="ep1-random", fansub="Fansub_D", url="magnet:rand"),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_A", "Fansub_B"])),
        ):
            result = await f.filter_batch(candidates)
        # Fansub_A has higher priority than Fansub_B → allowed
        # Fansub_D is unranked → skipped (Fansub_B is ranked and downloaded)
        assert len(result) == 1
        assert result[0].fansub == "Fansub_A"

    @pytest.mark.asyncio
    async def test_top_priority_downloaded_skips_all(self):
        """If top-priority fansub already downloaded, skip everything."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-ani", fansub="Fansub_B"),
            _make_resource(title="ep1-rand", fansub="Fansub_D", url="magnet:r"),
        ]
        downloaded = [_make_db_record(fansub="Fansub_A")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_A", "Fansub_B"])),
        ):
            result = await f.filter_batch(candidates)
        assert result == []


# ── language priority tests ──────────────────────────────────────────


class TestLanguagePriority:
    @pytest.mark.asyncio
    async def test_skip_lower_priority_language(self):
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-cht",
                languages=[LanguageType.CHT],
            ),
        ]
        downloaded = [_make_db_record(languages="简")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(languages=["简", "繁"], quality=[])),
        ):
            result = await f.filter_batch(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_allow_higher_priority_language(self):
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-chs", languages=[LanguageType.CHS]),
        ]
        downloaded = [_make_db_record(languages="繁")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(languages=["简", "繁"], quality=[])),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_multi_language_resource_uses_best_match(self):
        """A resource with [简, 日] should use 简 as its language level."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-dual",
                languages=[LanguageType.CHS, LanguageType.JP],
            ),
        ]
        downloaded = [_make_db_record(languages="繁")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(languages=["简", "繁"], quality=[])),
        ):
            result = await f.filter_batch(candidates)
        # 简 (idx=0) > 繁 (idx=1) → candidate is better → allowed
        assert len(result) == 1


# ── quality priority tests ───────────────────────────────────────────


class TestQualityPriority:
    @pytest.mark.asyncio
    async def test_default_quality_skip_lower(self):
        """Default: 1080p downloaded → skip 720p."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-720", quality=VideoQuality.Q720P),
        ]
        downloaded = [_make_db_record(quality="1080p")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config()),
        ):
            result = await f.filter_batch(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_default_quality_allow_higher(self):
        """Default: 720p downloaded → allow 1080p."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-1080", quality=VideoQuality.Q1080P),
        ]
        downloaded = [_make_db_record(quality="720p")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config()),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_quality_disabled(self):
        """quality = [] → no quality filtering."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-720", quality=VideoQuality.Q720P),
        ]
        downloaded = [_make_db_record(quality="1080p")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(quality=[])),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 1


# ── multi-field combination tests ────────────────────────────────────


class TestMultiFieldPriority:
    @pytest.mark.asyncio
    async def test_combined_fansub_and_language(self):
        """Skip when both fansub and language are lower priority."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-low",
                fansub="Fansub_C",
                languages=[LanguageType.CHT],
            ),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B", languages="简")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(
                _CFG_PRIORITY,
                _mock_config(
                    fansub=["Fansub_B", "Fansub_C"],
                    languages=["简", "繁"],
                    quality=[],
                ),
            ),
        ):
            result = await f.filter_batch(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_higher_priority_field_takes_precedence(self):
        """Better fansub (higher-priority field) overrides worse language."""
        f = ResourcePriorityFilter()
        # Better fansub, but worse language
        candidates = [
            _make_resource(
                title="ep1-mixed",
                fansub="Fansub_B",
                languages=[LanguageType.CHT],
            ),
        ]
        downloaded = [_make_db_record(fansub="Fansub_C", languages="简")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(
                _CFG_PRIORITY,
                _mock_config(
                    fansub=["Fansub_B", "Fansub_C"],
                    languages=["简", "繁"],
                    quality=[],
                ),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Fansub is checked first (field_order default): Fansub_B > Fansub_C → allow
        assert len(result) == 1
        assert result[0].fansub == "Fansub_B"

    @pytest.mark.asyncio
    async def test_lower_priority_field_breaks_tie(self):
        """When fansub is tied, language (lower-priority field) breaks the tie."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-cht",
                fansub="Fansub_B",
                languages=[LanguageType.CHT],
            ),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B", languages="简")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(
                _CFG_PRIORITY,
                _mock_config(
                    fansub=["Fansub_B", "Fansub_C"],
                    languages=["简", "繁"],
                    quality=[],
                ),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Fansub tied (both Fansub_B) → check language: 繁 < 简 → skip
        assert result == []


# ── version bypass tests ─────────────────────────────────────────────


class TestVersionBypass:
    @pytest.mark.asyncio
    async def test_higher_version_always_passes(self):
        """Version upgrade bypasses priority filtering."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-v2",
                fansub="Fansub_B",
                languages=[LanguageType.CHS],
                version=2,
            ),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B", languages="简", version=1)]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_A", "Fansub_B"])),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_same_version_no_bypass(self):
        """Same version does not get the bypass."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-v1-dup",
                fansub="Fansub_B",
                languages=[LanguageType.CHS],
                version=1,
            ),
        ]
        # Top-priority fansub already downloaded → should skip
        downloaded = [_make_db_record(fansub="Fansub_A", languages="简", version=1)]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(
                _CFG_PRIORITY,
                _mock_config(fansub=["Fansub_A", "Fansub_B"], quality=[]),
            ),
        ):
            result = await f.filter_batch(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_version_bypass_ignores_quality(self):
        """Version comparison ignores quality (per spec)."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-v2-720",
                fansub="Fansub_B",
                quality=VideoQuality.Q720P,
                languages=[LanguageType.CHS],
                version=2,
            ),
        ]
        # Same fansub+lang, different quality, lower version → bypass
        downloaded = [
            _make_db_record(
                fansub="Fansub_B", quality="1080p", languages="简", version=1
            )
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_B"])),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 1


# ── batch-internal selection tests ───────────────────────────────────


class TestBatchSelection:
    @pytest.mark.asyncio
    async def test_keeps_best_in_batch(self):
        """Within a single batch, only the lexicographically best survive."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-best",
                fansub="Fansub_B",
                quality=VideoQuality.Q1080P,
                url="magnet:best",
            ),
            _make_resource(
                title="ep1-worse",
                fansub="Fansub_C",
                quality=VideoQuality.Q720P,
                url="magnet:worse",
            ),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(
                _CFG_PRIORITY,
                _mock_config(fansub=["Fansub_B", "Fansub_C"]),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Fansub_B wins on fansub (first field) → only Fansub_B survives
        assert len(result) == 1
        assert result[0].title == "ep1-best"

    @pytest.mark.asyncio
    async def test_lexicographic_fansub_wins_over_quality(self):
        """With default field_order (fansub first), fansub decides."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-a",
                fansub="Fansub_B",
                quality=VideoQuality.Q720P,
                url="magnet:a",
            ),
            _make_resource(
                title="ep1-b",
                fansub="Fansub_C",
                quality=VideoQuality.Q1080P,
                url="magnet:b",
            ),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(
                _CFG_PRIORITY,
                _mock_config(fansub=["Fansub_B", "Fansub_C"]),
            ),
        ):
            result = await f.filter_batch(candidates)
        # fansub is checked first: Fansub_B > Fansub_C → only Fansub_B survives
        assert len(result) == 1
        assert result[0].title == "ep1-a"

    @pytest.mark.asyncio
    async def test_keeps_ties(self):
        """Candidates with identical priority levels both survive."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-a",
                fansub="Fansub_B",
                quality=VideoQuality.Q1080P,
                url="magnet:a",
            ),
            _make_resource(
                title="ep1-b",
                fansub="Fansub_B",
                quality=VideoQuality.Q1080P,
                url="magnet:b",
            ),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(
                _CFG_PRIORITY,
                _mock_config(fansub=["Fansub_B", "Fansub_C"]),
            ),
        ):
            result = await f.filter_batch(candidates)
        assert len(result) == 2


# ── edge case tests ──────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_priority_config_passes_all(self):
        """All priority lists empty → everything passes."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="a", url="magnet:a"),
            _make_resource(title="b", url="magnet:b"),
        ]

        with patch(_CFG_PRIORITY, _mock_config(fansub=[], languages=[], quality=[])):
            result = await f.filter_batch(candidates)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_batch(self):
        """Empty batch returns empty list."""
        f = ResourcePriorityFilter()
        result = await f.filter_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_no_db_records_passes_all(self):
        """No previous downloads → all candidates pass."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-a", fansub="Fansub_B", url="magnet:a"),
            _make_resource(title="ep1-b", fansub="Fansub_C", url="magnet:b"),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_B", "Fansub_C"])),
        ):
            result = await f.filter_batch(candidates)
        # No DB records → batch selection: Fansub_B > Fansub_C on fansub → only Fansub_B
        assert len(result) == 1
        assert result[0].fansub == "Fansub_B"

    @pytest.mark.asyncio
    async def test_candidate_not_in_priority_list(self):
        """Unranked candidate gets skipped when ranked value downloaded."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="ep1-unknown", fansub="Fansub_X"),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_B"])),
        ):
            result = await f.filter_batch(candidates)
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_metadata_bypasses_filter(self):
        """Entries without anime_name/season/episode bypass priority filtering."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(title="no-meta"),
        ]
        candidates[0].anime_name = None

        with patch(_CFG_PRIORITY, _mock_config(fansub=["Fansub_B"])):
            result = await f.filter_batch(candidates)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_different_episodes_independent(self):
        """Priority is per-episode; different episodes are independent."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-other", fansub="Fansub_C", episode=1, url="magnet:e1"
            ),
            _make_resource(
                title="ep2-other", fansub="Fansub_C", episode=2, url="magnet:e2"
            ),
        ]

        _mock_find = AsyncMock(
            side_effect=lambda anime_name, season, episode: (
                [_make_db_record(fansub="Fansub_B")] if episode == 1 else []
            )
        )

        with (
            patch(_DB_FIND, new=_mock_find),
            patch(
                _CFG_PRIORITY,
                _mock_config(fansub=["Fansub_B", "Fansub_C"], quality=[]),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Episode 1: Fansub_B already downloaded → Fansub_C skipped
        # Episode 2: no downloads → Fansub_C passes
        assert len(result) == 1
        assert result[0].episode == 2


# ── field_order customization tests ──────────────────────────────────


class TestFieldOrder:
    @pytest.mark.asyncio
    async def test_language_first_overrides_fansub(self):
        """With field_order=[languages, fansub, quality], language decides first."""
        f = ResourcePriorityFilter()
        # Worse fansub, but better language
        candidates = [
            _make_resource(
                title="ep1-lang-better",
                fansub="Fansub_C",
                languages=[LanguageType.CHS],
            ),
        ]
        downloaded = [_make_db_record(fansub="Fansub_B", languages="繁")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(
                _CFG_PRIORITY,
                _mock_config(
                    fansub=["Fansub_B", "Fansub_C"],
                    languages=["简", "繁"],
                    quality=[],
                    field_order=["languages", "fansub", "quality"],
                ),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Language is checked first: 简 > 繁 → candidate is better → allow
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_quality_first_in_batch(self):
        """With field_order=[quality, fansub, languages], quality decides batch selection."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-a",
                fansub="Fansub_B",
                quality=VideoQuality.Q720P,
                url="magnet:a",
            ),
            _make_resource(
                title="ep1-b",
                fansub="Fansub_C",
                quality=VideoQuality.Q1080P,
                url="magnet:b",
            ),
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=[]),
            patch(
                _CFG_PRIORITY,
                _mock_config(
                    fansub=["Fansub_B", "Fansub_C"],
                    field_order=["quality", "fansub", "languages"],
                ),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Quality checked first: 1080p > 720p → Fansub_C wins
        assert len(result) == 1
        assert result[0].title == "ep1-b"

    @pytest.mark.asyncio
    async def test_single_field_order(self):
        """field_order with only one field ignores the others."""
        f = ResourcePriorityFilter()
        # Worse quality, but only fansub in field_order
        candidates = [
            _make_resource(
                title="ep1-low-q",
                fansub="Fansub_B",
                quality=VideoQuality.Q480P,
            ),
        ]
        downloaded = [_make_db_record(fansub="Fansub_C", quality="1080p")]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=downloaded),
            patch(
                _CFG_PRIORITY,
                _mock_config(
                    fansub=["Fansub_B", "Fansub_C"],
                    field_order=["fansub"],
                ),
            ),
        ):
            result = await f.filter_batch(candidates)
        # Only fansub is checked: Fansub_B > Fansub_C → allow (quality ignored)
        assert len(result) == 1


# ── pending tracking tests (cross-batch) ─────────────────────────────


class TestPreInsertedDBFiltering:
    """Test that pre-inserted DB records (from in-flight downloads) prevent duplicates.

    With the pre-insert-to-DB approach, accepted entries are written to DB
    before download starts.  Subsequent batches see them via
    find_resources_by_episode.
    """

    @pytest.mark.asyncio
    async def test_preinserted_blocks_lower_priority_language(self):
        """简日 pre-inserted to DB → 繁日 candidate should be skipped."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-cht",
                fansub="Fansub_A",
                languages=[LanguageType.CHT],
                url="magnet:cht",
            ),
        ]
        cfg = _mock_config(
            fansub=["Fansub_A"],
            languages=["简", "繁"],
        )
        # Simulate pre-inserted record from a previous batch.
        db_records = [
            {
                "fansub": "Fansub_A",
                "quality": "1080p",
                "languages": "简日",
                "version": 1,
            }
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG_PRIORITY, cfg),
        ):
            result = await f.filter_batch(candidates)
            assert result == []

    @pytest.mark.asyncio
    async def test_preinserted_allows_higher_priority(self):
        """繁日 pre-inserted to DB → 简日 candidate (higher priority) still passes."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-chs",
                fansub="Fansub_A",
                languages=[LanguageType.CHS],
                url="magnet:chs",
            ),
        ]
        cfg = _mock_config(
            fansub=["Fansub_A"],
            languages=["简", "繁"],
        )
        db_records = [
            {
                "fansub": "Fansub_A",
                "quality": "1080p",
                "languages": "繁日",
                "version": 1,
            }
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG_PRIORITY, cfg),
        ):
            result = await f.filter_batch(candidates)
            assert len(result) == 1
            assert result[0].title == "ep1-chs"

    @pytest.mark.asyncio
    async def test_preinserted_fansub_blocks_lower_fansub(self):
        """Fansub_A pre-inserted to DB → Fansub_B candidate should be skipped."""
        f = ResourcePriorityFilter()
        candidates = [
            _make_resource(
                title="ep1-fanB",
                fansub="Fansub_B",
                url="magnet:b",
            ),
        ]
        cfg = _mock_config(fansub=["Fansub_A", "Fansub_B"])
        db_records = [
            {"fansub": "Fansub_A", "quality": "1080p", "languages": "", "version": 1}
        ]

        with (
            patch(_DB_FIND, new_callable=AsyncMock, return_value=db_records),
            patch(_CFG_PRIORITY, cfg),
        ):
            result = await f.filter_batch(candidates)
            assert result == []
