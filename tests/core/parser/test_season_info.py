"""Tests for SeasonInfo.from_raw_list() method."""

import pytest

from openlist_ani.core.parser.model import SeasonInfo


class TestSeasonInfoFromRawList:
    """Test SeasonInfo.from_raw_list() static method."""

    def test_empty_list_returns_empty(self):
        """Empty input should return empty list."""
        result = SeasonInfo.from_raw_list([])
        assert result == []

    def test_single_element_list(self):
        """Single element should work correctly."""
        raw_seasons = [{"season_number": 1, "episode_count": 12, "name": "Season 1"}]
        result = SeasonInfo.from_raw_list(raw_seasons)

        assert len(result) == 1
        assert result[0].season_number == 1
        assert result[0].episode_count == 12
        assert result[0].name == "Season 1"

    def test_sorting_by_season_number(self):
        """Items should be sorted by season_number regardless of input order."""
        raw_seasons = [
            {"season_number": 3, "episode_count": 10, "name": "Season 3"},
            {"season_number": 1, "episode_count": 12, "name": "Season 1"},
            {"season_number": 2, "episode_count": 8, "name": "Season 2"},
        ]
        result = SeasonInfo.from_raw_list(raw_seasons)

        assert len(result) == 3
        assert result[0].season_number == 1
        assert result[1].season_number == 2
        assert result[2].season_number == 3
        assert result[0].name == "Season 1"
        assert result[1].name == "Season 2"
        assert result[2].name == "Season 3"

    def test_missing_fields_use_defaults(self):
        """Missing fields should use default values."""
        raw_seasons = [
            # Missing name -> should default to ""
            {"season_number": 1, "episode_count": 12},
            # Missing episode_count -> should default to 0
            {"season_number": 2, "name": "Special"},
            # Missing season_number -> should default to 0
            {"episode_count": 5, "name": "OVA"},
            # All fields missing -> all defaults
            {},
        ]
        result = SeasonInfo.from_raw_list(raw_seasons)

        assert len(result) == 4

        # Sort order: season 0 items first, then 1, then 2
        # Two items have season_number=0, they maintain relative order
        assert result[0].season_number == 0  # OVA
        assert result[0].episode_count == 5
        assert result[0].name == "OVA"

        assert result[1].season_number == 0  # all missing
        assert result[1].episode_count == 0
        assert result[1].name == ""

        assert result[2].season_number == 1  # missing name
        assert result[2].episode_count == 12
        assert result[2].name == ""

        assert result[3].season_number == 2  # missing episode_count
        assert result[3].episode_count == 0
        assert result[3].name == "Special"

    def test_partial_fields_mixed_with_complete(self):
        """Mix of complete and incomplete records."""
        raw_seasons = [
            {"season_number": 2, "episode_count": 10},  # missing name
            {"season_number": 1, "episode_count": 12, "name": "First Season"},  # complete
        ]
        result = SeasonInfo.from_raw_list(raw_seasons)

        assert len(result) == 2
        assert result[0].season_number == 1
        assert result[0].episode_count == 12
        assert result[0].name == "First Season"

        assert result[1].season_number == 2
        assert result[1].episode_count == 10
        assert result[1].name == ""