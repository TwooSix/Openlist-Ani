"""Tests for episode mapping, cour-based mapping, and cour detection."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openlist_ani.core.parser.cour.detector import detect_cours_from_episodes
from openlist_ani.core.parser.model import SeasonInfo
from openlist_ani.core.parser.tmdb.episode_mapper import (
    CourMappingStrategy,
    EpisodeMapper,
    MappingContext,
    _map_absolute_episode,
)


def _make_season(num: int, eps: int, name: str = "") -> dict:
    return {"season_number": num, "episode_count": eps, "name": name or f"Season {num}"}


def _make_episode(ep_num: int, air_date: str) -> dict:
    """Helper to create a fake TMDB episode dict."""
    return {"episode_number": ep_num, "air_date": air_date}


# ---------------------------------------------------------------------------
# Convenience wrappers that build MappingContext for tests
# ---------------------------------------------------------------------------


async def _verify_tmdb_season_episode(
    tmdb_client,
    tmdb_id,
    season,
    episode,
    anime_name=None,
) -> tuple[int, int] | None:
    """End-to-end helper: fetches TMDB details, runs all strategies."""
    details = await tmdb_client.get_tv_show_details(tmdb_id)
    if not details:
        return None
    raw_seasons = details.get("seasons", [])
    sorted_seasons = SeasonInfo.from_raw_list(raw_seasons)
    mapper = EpisodeMapper()
    ctx = MappingContext(
        tmdb_id=tmdb_id,
        fansub_season=season,
        fansub_episode=episode,
        sorted_seasons=sorted_seasons,
        tmdb_client=tmdb_client,
    )
    mapping = await mapper.map(ctx)
    if mapping:
        return mapping.season, mapping.episode
    return None


async def _code_cour_mapping(
    tmdb_client,
    tmdb_id,
    sorted_seasons_raw,
    fansub_season,
    fansub_episode,
) -> tuple[int, int] | None:
    """Isolated CourMappingStrategy test helper."""
    sorted_seasons = SeasonInfo.from_raw_list(sorted_seasons_raw)
    strategy = CourMappingStrategy()
    ctx = MappingContext(
        tmdb_id=tmdb_id,
        fansub_season=fansub_season,
        fansub_episode=fansub_episode,
        sorted_seasons=sorted_seasons,
        tmdb_client=tmdb_client,
    )
    mapping = await strategy.try_map(ctx)
    if mapping:
        return mapping.season, mapping.episode
    return None


def _test_map_absolute_episode(
    episode_abs: int, sorted_seasons_raw: list[dict[str, Any]]
) -> tuple[int, int] | None:
    """Helper for _map_absolute_episode."""
    sorted_seasons = SeasonInfo.from_raw_list(sorted_seasons_raw)
    mapping = _map_absolute_episode(episode_abs, sorted_seasons)
    if mapping:
        return mapping.season, mapping.episode
    return None


# =========================================================================
# DirectMatchStrategy — fast path
# =========================================================================


class TestDirectMatch:
    """When season/episode is valid in TMDB, returns immediately."""

    @pytest.mark.asyncio
    async def test_valid_season_episode_passes_through(self):
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [{"season_number": 1, "episode_count": 12}]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=5)
        assert result == (1, 5)

    @pytest.mark.asyncio
    async def test_last_episode_of_season(self):
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [{"season_number": 1, "episode_count": 12}]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=12)
        assert result == (1, 12)

    @pytest.mark.asyncio
    async def test_no_details_returns_none(self):
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {}
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=5)
        assert result is None


# =========================================================================
# SpecialEpisodeStrategy
# =========================================================================


class TestSpecialEpisode:
    """Episode==0 mapping to Season 0 (Specials)."""

    @pytest.mark.asyncio
    async def test_episode_zero_with_specials_falls_back_to_s0e1(self):
        """Episode 0 with specials available, no LLM → fallback to S0E1."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 0, "episode_count": 3, "name": "Specials"},
                {"season_number": 1, "episode_count": 12},
            ]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=0)
        assert result == (0, 1)

    @pytest.mark.asyncio
    async def test_episode_zero_no_specials_returns_passthrough(self):
        """Episode 0 without Season 0 → passthrough (S1E0)."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [{"season_number": 1, "episode_count": 12}]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=0)
        assert result == (0, 0)

    @pytest.mark.asyncio
    async def test_episode_zero_with_llm_matches_special(self):
        """Episode 0 with LLM + resource_title → LLM picks best special episode."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 0, "episode_count": 3, "name": "Specials"},
                {"season_number": 1, "episode_count": 12},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = [
            {
                "episode_number": 1,
                "name": "OVA 1",
                "overview": "First OVA",
                "air_date": "2023-06-01",
            },
            {
                "episode_number": 2,
                "name": "OVA 2",
                "overview": "Second OVA",
                "air_date": "2023-12-01",
            },
            {
                "episode_number": 3,
                "name": "SP - Beach Episode",
                "overview": "Special",
                "air_date": "2024-03-01",
            },
        ]

        # Mock LLM to return episode 2
        mock_llm = AsyncMock()
        mock_llm.chat_completion.return_value = '{"episode_number": 2}'

        sorted_seasons = SeasonInfo.from_raw_list(
            mock_tmdb.get_tv_show_details.return_value["seasons"]
        )
        mapper = EpisodeMapper()
        ctx = MappingContext(
            tmdb_id=100,
            fansub_season=1,
            fansub_episode=0,
            sorted_seasons=sorted_seasons,
            tmdb_client=mock_tmdb,
            resource_title="[Fansub] Anime Name OVA 2 [1080P]",
            llm_client=mock_llm,
        )
        mapping = await mapper.map(ctx)
        assert mapping is not None
        assert mapping.season == 0
        assert mapping.episode == 2
        assert mapping.strategy == "special_llm"

    @pytest.mark.asyncio
    async def test_episode_zero_llm_returns_null_falls_back(self):
        """LLM returns null → fallback to S0E1."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 0, "episode_count": 2, "name": "Specials"},
                {"season_number": 1, "episode_count": 12},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = [
            {
                "episode_number": 1,
                "name": "OVA 1",
                "overview": "",
                "air_date": "2023-06-01",
            },
        ]

        mock_llm = AsyncMock()
        mock_llm.chat_completion.return_value = '{"episode_number": null}'

        sorted_seasons = SeasonInfo.from_raw_list(
            mock_tmdb.get_tv_show_details.return_value["seasons"]
        )
        mapper = EpisodeMapper()
        ctx = MappingContext(
            tmdb_id=100,
            fansub_season=1,
            fansub_episode=0,
            sorted_seasons=sorted_seasons,
            tmdb_client=mock_tmdb,
            resource_title="[Fansub] Anime Name SP [1080P]",
            llm_client=mock_llm,
        )
        mapping = await mapper.map(ctx)
        assert mapping is not None
        assert mapping.season == 0
        assert mapping.episode == 1
        assert mapping.strategy == "special_fallback"


# =========================================================================
# CourMappingStrategy — fansub 自行分季
# =========================================================================


class TestCourMapping:
    """Tests for cour-based mapping (both relative and absolute approaches)."""

    @pytest.mark.asyncio
    async def test_returns_none_when_fansub_season_within_tmdb_range(self):
        """Cour mapping should not activate when fansub season exists in TMDB."""
        sorted_seasons = [_make_season(1, 12), _make_season(2, 12)]
        mock_tmdb = AsyncMock()
        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=1, fansub_episode=5
        )
        assert result is None
        mock_tmdb.get_season_episodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_for_no_regular_seasons(self):
        sorted_seasons = [_make_season(0, 5, "Specials")]
        mock_tmdb = AsyncMock()
        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=2, fansub_episode=3
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_relative_two_cours_in_one_season(self):
        """TMDB S1(24 eps, 2 cours) → fansub S2E3 = TMDB S1E15 (relative)."""
        sorted_seasons = [_make_season(1, 24)]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2024-01-{i - 12:02d}") for i in range(13, 25)]

        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=2, fansub_episode=3
        )
        assert result == (1, 15)  # cour2 starts at ep13, 13+3-1=15

    @pytest.mark.asyncio
    async def test_relative_three_cours_oshi_no_ko(self):
        """Oshi no Ko: TMDB S1(35 eps, 3 cours) → fansub S3E6 = S1E29."""
        sorted_seasons = [
            _make_season(0, 1, "Specials"),
            _make_season(1, 35),
        ]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = (
            [_make_episode(i, f"2023-04-{i:02d}") for i in range(1, 12)]
            + [_make_episode(i, f"2024-07-{i - 11:02d}") for i in range(12, 24)]
            + [_make_episode(i, f"2026-01-{i - 23:02d}") for i in range(24, 36)]
        )

        result = await _code_cour_mapping(
            mock_tmdb, 203737, sorted_seasons, fansub_season=3, fansub_episode=6
        )
        assert result == (1, 29)
        mock_tmdb.get_season_episodes.assert_called_once_with(203737, 1)

    @pytest.mark.asyncio
    async def test_absolute_solo_leveling(self):
        """Solo Leveling: TMDB S1(25 eps, 2 cours), fansub S02E14 → S01E14 (absolute)."""
        sorted_seasons = [_make_season(1, 25)]
        mock_tmdb = AsyncMock()
        # 2 cours: ep 1-12 (Apr 2024), ep 13-25 (Jan 2025)
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2024-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2025-01-{i - 12:02d}") for i in range(13, 26)]

        # S02E14 → relative = 13+14-1 = 26 > 25 → absolute: 14 in [13,25] → S1E14
        result = await _code_cour_mapping(
            mock_tmdb, 127532, sorted_seasons, fansub_season=2, fansub_episode=14
        )
        assert result == (1, 14)

    @pytest.mark.asyncio
    async def test_absolute_solo_leveling_last_episode(self):
        """Solo Leveling: fansub S02E25 → S01E25."""
        sorted_seasons = [_make_season(1, 25)]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2024-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2025-01-{i - 12:02d}") for i in range(13, 26)]

        result = await _code_cour_mapping(
            mock_tmdb, 127532, sorted_seasons, fansub_season=2, fansub_episode=25
        )
        assert result == (1, 25)

    @pytest.mark.asyncio
    async def test_absolute_episode_in_cour_range(self):
        """Relative target exceeds count, but fansub_episode in cour range → absolute."""
        sorted_seasons = [_make_season(1, 24)]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2024-01-{i - 12:02d}") for i in range(13, 25)]

        # fansub S2E15 → relative = 13+15-1 = 27 > 24 → 15 in [13,24] → S1E15
        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=2, fansub_episode=15
        )
        assert result == (1, 15)

    @pytest.mark.asyncio
    async def test_cour_index_out_of_range(self):
        """Fansub S4 but only 2 cours exist → returns None."""
        sorted_seasons = [_make_season(1, 24)]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2024-01-{i - 12:02d}") for i in range(13, 25)]

        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=4, fansub_episode=1
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_single_cour_season_treated_as_one_cour(self):
        """Single-cour S1(12 eps) → fansub S2E3 → cour_idx=1, out of range → None."""
        sorted_seasons = [_make_season(1, 12)]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ]

        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=2, fansub_episode=3
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_multi_season_multi_cour(self):
        """TMDB S1(24, 2 cours) + S2(12, 1 cour) → 3 global cours."""
        sorted_seasons = [_make_season(1, 24), _make_season(2, 12)]
        mock_tmdb = AsyncMock()

        async def mock_get_episodes(tmdb_id, snum):
            if snum == 1:
                return [_make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)] + [
                    _make_episode(i, f"2024-01-{i - 12:02d}") for i in range(13, 25)
                ]
            elif snum == 2:
                return [_make_episode(i, f"2025-04-{i:02d}") for i in range(1, 13)]
            return []

        mock_tmdb.get_season_episodes = mock_get_episodes

        # fansub S3E5 → global cour 3 = S2 cour1 → S2E(1+5-1) = S2E5
        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=3, fansub_episode=5
        )
        assert result == (2, 5)

    @pytest.mark.asyncio
    async def test_no_episodes_returned_uses_whole_season_as_cour(self):
        """When episode details are unavailable, treat entire season as one cour."""
        sorted_seasons = [_make_season(1, 12)]
        mock_tmdb = AsyncMock()
        mock_tmdb.get_season_episodes.return_value = []

        # S1 treated as 1 cour, fansub S2E3 → cour_idx=1, out of range → None
        result = await _code_cour_mapping(
            mock_tmdb, 100, sorted_seasons, fansub_season=2, fansub_episode=3
        )
        assert result is None


# =========================================================================
# AbsoluteEpisodeStrategy — _map_absolute_episode
# =========================================================================


class TestMapAbsoluteEpisode:
    def test_maps_to_second_season(self):
        seasons = [_make_season(1, 12), _make_season(2, 12)]
        assert _test_map_absolute_episode(15, seasons) == (2, 3)

    def test_episode_zero_returns_none(self):
        seasons = [_make_season(1, 12)]
        assert _test_map_absolute_episode(0, seasons) is None

    def test_episode_exceeds_total_returns_none(self):
        seasons = [_make_season(1, 12)]
        assert _test_map_absolute_episode(100, seasons) is None

    def test_ignores_season_zero(self):
        seasons = [_make_season(0, 5), _make_season(1, 12)]
        assert _test_map_absolute_episode(3, seasons) == (1, 3)


# =========================================================================
# AbsoluteEpisodeStrategy — integration tests
# =========================================================================


class TestAbsoluteEpisode:
    """Fansub S01 with accumulated episodes, TMDB has multiple seasons."""

    @pytest.mark.asyncio
    async def test_absolute_episode_within_range(self):
        """Absolute episode 15 → S2E3 when S1 has 12 episodes."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 12},
                {"season_number": 2, "episode_count": 12},
            ]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=15)
        assert result == (2, 3)

    @pytest.mark.asyncio
    async def test_absolute_episode_exceeds_total(self):
        """Absolute episode 25 exceeds total (24) → returns None."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 12},
                {"season_number": 2, "episode_count": 12},
            ]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=25)
        assert result is None

    @pytest.mark.asyncio
    async def test_season3_direct_match(self):
        """Fansub S3E6, TMDB has S1(12), S2(12), S3(12) → direct match S3E6."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 12},
                {"season_number": 2, "episode_count": 12},
                {"season_number": 3, "episode_count": 12},
            ]
        }
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=3, episode=6)
        assert result == (3, 6)

    @pytest.mark.asyncio
    async def test_nonexistent_season_returns_none(self):
        """Fansub S3E6, TMDB has only S1+S2 → all strategies fail → None."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 12},
                {"season_number": 2, "episode_count": 12},
            ]
        }
        # Need to mock get_season_episodes for CourMappingStrategy
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ]
        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=3, episode=6)
        assert result is None


# =========================================================================
# detect_cours_from_episodes — cour boundary detection
# =========================================================================


class TestDetectCoursFromEpisodes:
    def test_single_cour_no_gap(self):
        episodes = [_make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)]
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 1
        assert cours[0].start_episode == 1
        assert cours[0].end_episode == 12

    def test_two_cours_with_gap(self):
        episodes = [_make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)] + [
            _make_episode(i, f"2024-01-{i - 12:02d}") for i in range(13, 25)
        ]
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 2
        assert cours[0].start_episode == 1
        assert cours[0].end_episode == 12
        assert cours[1].start_episode == 13
        assert cours[1].end_episode == 24

    def test_three_cours_like_oshi_no_ko(self):
        episodes = (
            [_make_episode(i, f"2023-04-{i:02d}") for i in range(1, 12)]
            + [_make_episode(i, f"2024-07-{i - 11:02d}") for i in range(12, 24)]
            + [_make_episode(i, f"2026-01-{i - 23:02d}") for i in range(24, 36)]
        )
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 3
        assert cours[0].end_episode == 11
        assert cours[1].start_episode == 12
        assert cours[2].start_episode == 24

    def test_empty_episodes(self):
        assert detect_cours_from_episodes([]) == []

    def test_episodes_without_air_dates_skipped(self):
        episodes = [
            {"episode_number": 1, "air_date": None},
            _make_episode(2, "2023-04-08"),
        ]
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 1
        assert cours[0].start_episode == 2

    def test_custom_gap_days(self):
        episodes = [
            _make_episode(1, "2023-04-01"),
            _make_episode(2, "2023-04-08"),
            _make_episode(3, "2023-05-20"),
            _make_episode(4, "2023-05-27"),
        ]
        assert len(detect_cours_from_episodes(episodes, gap_days=60)) == 1
        # 42-day gap between ep2 and ep3
        assert len(detect_cours_from_episodes(episodes, gap_days=30)) == 2

    def test_cour_air_date_range_format(self):
        """Verify CourGroup includes correct air_date_start/end."""
        episodes = [
            _make_episode(1, "2023-04-01"),
            _make_episode(2, "2023-04-08"),
            _make_episode(3, "2023-04-15"),
        ]
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 1
        assert cours[0].air_date_start == "2023-04-01"
        assert cours[0].air_date_end == "2023-04-15"

    def test_unsorted_episodes_are_handled(self):
        episodes = [
            _make_episode(12, "2023-06-24"),
            _make_episode(1, "2023-04-01"),
            _make_episode(6, "2023-05-06"),
            _make_episode(13, "2024-01-06"),
            _make_episode(18, "2024-02-10"),
            _make_episode(24, "2024-03-23"),
        ]
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 2
        assert cours[0].start_episode == 1
        assert cours[0].end_episode == 12
        assert cours[1].start_episode == 13
        assert cours[1].end_episode == 24

    def test_invalid_air_date_format_skipped(self):
        episodes = [
            _make_episode(1, "invalid-date"),
            _make_episode(2, "2023-04-08"),
        ]
        cours = detect_cours_from_episodes(episodes)
        assert len(cours) == 1
        assert cours[0].start_episode == 2


# =========================================================================
# End-to-end integration — full mapper with cour detection
# =========================================================================


class TestIntegrationWithCourDetection:
    """When fansub season doesn't exist in TMDB, cour detection kicks in."""

    @pytest.mark.asyncio
    async def test_fansub_season3_mapped_via_cour_detection(self):
        """Oshi no Ko: TMDB S1(35 eps, 3 cours), fansub S3E06 → S1E29."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 0, "episode_count": 1, "name": "Specials"},
                {"season_number": 1, "episode_count": 35, "name": "Season 1"},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = (
            [_make_episode(i, f"2023-04-{i:02d}") for i in range(1, 12)]
            + [_make_episode(i, f"2024-07-{i - 11:02d}") for i in range(12, 24)]
            + [_make_episode(i, f"2026-01-{i - 23:02d}") for i in range(24, 36)]
        )

        result = await _verify_tmdb_season_episode(
            mock_tmdb, 203737, season=3, episode=6, anime_name="【我推的孩子】"
        )
        assert result == (1, 29)
        mock_tmdb.get_season_episodes.assert_called_once_with(203737, 1)

    @pytest.mark.asyncio
    async def test_cour_mapping_for_two_cour_season(self):
        """TMDB S1(24 eps, 2 cours), fansub S2E3 → S1E15."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 24, "name": "Season 1"},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2024-01-{i - 12:02d}") for i in range(13, 25)]

        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=2, episode=3)
        assert result == (1, 15)

    @pytest.mark.asyncio
    async def test_no_cour_detection_when_fansub_season_exists(self):
        """When fansub season exists in TMDB but episode is out of range → absolute."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 12},
                {"season_number": 2, "episode_count": 12},
            ]
        }

        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=1, episode=15)
        # S1E15 → absolute mapping → S2E3
        assert result == (2, 3)
        mock_tmdb.get_season_episodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_cour_season_falls_through_to_none(self):
        """Single-cour S1(12 eps), fansub S2E3 → all strategies fail → None."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 12, "name": "Season 1"},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2023-04-{i:02d}") for i in range(1, 13)
        ]

        result = await _verify_tmdb_season_episode(mock_tmdb, 100, season=2, episode=3)
        assert result is None

    @pytest.mark.asyncio
    async def test_solo_leveling_e2e(self):
        """Solo Leveling S02E14~E25 → S01E14~E25 via cour absolute mapping."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 1, "episode_count": 25, "name": "Season 1"},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = [
            _make_episode(i, f"2024-04-{i:02d}") for i in range(1, 13)
        ] + [_make_episode(i, f"2025-01-{i - 12:02d}") for i in range(13, 26)]

        for fansub_ep in range(14, 26):
            result = await _verify_tmdb_season_episode(
                mock_tmdb, 127532, season=2, episode=fansub_ep
            )
            assert result == (
                1,
                fansub_ep,
            ), f"S02E{fansub_ep} should map to S01E{fansub_ep}"

    @pytest.mark.asyncio
    async def test_no_episodes_api_returns_none(self):
        """TMDB API returns no episode details → cour detection impossible → None."""
        mock_tmdb = AsyncMock()
        mock_tmdb.get_tv_show_details.return_value = {
            "seasons": [
                {"season_number": 0, "episode_count": 1, "name": "Specials"},
                {"season_number": 1, "episode_count": 35, "name": "Season 1"},
            ]
        }
        mock_tmdb.get_season_episodes.return_value = []

        result = await _verify_tmdb_season_episode(
            mock_tmdb, 203737, season=3, episode=1, anime_name="【我推的孩子】"
        )
        assert result is None
