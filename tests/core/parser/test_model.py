"""Tests for openlist_ani.core.parser.model module."""

import json

import pytest
from pydantic import ValidationError

from openlist_ani.core.parser.model import ResourceTitleParseResult
from openlist_ani.core.website.model import LanguageType, VideoQuality


class TestResourceTitleParseResult:
    """Test ResourceTitleParseResult model validation and construction."""

    # --- Valid construction ---

    def test_valid_full_construction(self):
        result = ResourceTitleParseResult(
            anime_name="葬送のフリーレン",
            season=1,
            episode=5,
            quality=VideoQuality.Q1080P,
            fansub="喵萌奶茶屋",
            languages=[LanguageType.CHS, LanguageType.JP],
            version=1,
            tmdb_id=209867,
        )
        assert result.anime_name == "葬送のフリーレン"
        assert result.season == 1
        assert result.episode == 5
        assert result.quality == VideoQuality.Q1080P
        assert result.fansub == "喵萌奶茶屋"
        assert result.languages == [LanguageType.CHS, LanguageType.JP]
        assert result.version == 1
        assert result.tmdb_id == 209867

    def test_valid_minimal_with_optional_none(self):
        result = ResourceTitleParseResult(
            anime_name="Test",
            season=1,
            episode=1,
            quality=None,
            fansub=None,
            languages=[],
            version=1,
        )
        assert result.quality is None
        assert result.fansub is None
        assert result.tmdb_id is None
        assert result.languages == []

    def test_tmdb_id_defaults_to_none(self):
        result = ResourceTitleParseResult(
            anime_name="Test",
            season=0,
            episode=1,
            quality=VideoQuality.UNKNOWN,
            fansub=None,
            languages=[LanguageType.UNKNOWN],
            version=1,
        )
        assert result.tmdb_id is None

    # --- model_validate_json (mirroring real LLM output) ---

    def test_model_validate_json_full(self):
        raw = json.dumps(
            {
                "anime_name": "Frieren",
                "season": 1,
                "episode": 12,
                "quality": "1080p",
                "fansub": "SubGroup",
                "languages": ["简", "日"],
                "version": 2,
                "tmdb_id": 12345,
            }
        )
        result = ResourceTitleParseResult.model_validate_json(raw)
        assert result.anime_name == "Frieren"
        assert result.quality == VideoQuality.Q1080P
        assert result.languages == [LanguageType.CHS, LanguageType.JP]
        assert result.version == 2
        assert result.tmdb_id == 12345

    def test_model_validate_json_without_tmdb_id(self):
        raw = json.dumps(
            {
                "anime_name": "Test",
                "season": 1,
                "episode": 1,
                "quality": "unknown",
                "fansub": None,
                "languages": ["未知"],
                "version": 1,
            }
        )
        result = ResourceTitleParseResult.model_validate_json(raw)
        assert result.tmdb_id is None

    def test_model_validate_json_special_episode(self):
        """Season 0 for specials is valid."""
        raw = json.dumps(
            {
                "anime_name": "Test",
                "season": 0,
                "episode": 1,
                "quality": "720p",
                "fansub": None,
                "languages": [],
                "version": 1,
            }
        )
        result = ResourceTitleParseResult.model_validate_json(raw)
        assert result.season == 0

    def test_model_validate_json_all_quality_variants(self):
        """All VideoQuality enum values should be accepted."""
        for q in VideoQuality:
            raw = json.dumps(
                {
                    "anime_name": "Test",
                    "season": 1,
                    "episode": 1,
                    "quality": q.value,
                    "fansub": None,
                    "languages": [],
                    "version": 1,
                }
            )
            result = ResourceTitleParseResult.model_validate_json(raw)
            assert result.quality == q

    def test_model_validate_json_all_language_variants(self):
        """All LanguageType enum values should be accepted."""
        for lang in LanguageType:
            raw = json.dumps(
                {
                    "anime_name": "Test",
                    "season": 1,
                    "episode": 1,
                    "quality": "1080p",
                    "fansub": None,
                    "languages": [lang.value],
                    "version": 1,
                }
            )
            result = ResourceTitleParseResult.model_validate_json(raw)
            assert lang in result.languages

    # --- Validation errors (bad input) ---

    def test_missing_required_anime_name(self):
        with pytest.raises(ValidationError):
            ResourceTitleParseResult(
                season=1,  # type: ignore[call-arg]
                episode=1,
                quality=None,
                fansub=None,
                languages=[],
                version=1,
            )

    def test_missing_required_season(self):
        with pytest.raises(ValidationError):
            ResourceTitleParseResult(
                anime_name="Test",  # type: ignore[call-arg]
                episode=1,
                quality=None,
                fansub=None,
                languages=[],
                version=1,
            )

    def test_missing_required_episode(self):
        with pytest.raises(ValidationError):
            ResourceTitleParseResult(
                anime_name="Test",  # type: ignore[call-arg]
                season=1,
                quality=None,
                fansub=None,
                languages=[],
                version=1,
            )

    def test_invalid_quality_value(self):
        with pytest.raises(ValidationError):
            ResourceTitleParseResult.model_validate_json(
                json.dumps(
                    {
                        "anime_name": "Test",
                        "season": 1,
                        "episode": 1,
                        "quality": "4K",  # not a valid enum value
                        "fansub": None,
                        "languages": [],
                        "version": 1,
                    }
                )
            )

    def test_invalid_language_value(self):
        with pytest.raises(ValidationError):
            ResourceTitleParseResult.model_validate_json(
                json.dumps(
                    {
                        "anime_name": "Test",
                        "season": 1,
                        "episode": 1,
                        "quality": "1080p",
                        "fansub": None,
                        "languages": ["deutsch"],  # not a valid enum value
                        "version": 1,
                    }
                )
            )

    def test_invalid_json_string(self):
        with pytest.raises((ValidationError, ValueError)):
            ResourceTitleParseResult.model_validate_json("not json at all")

    def test_empty_json_object(self):
        with pytest.raises(ValidationError):
            ResourceTitleParseResult.model_validate_json("{}")

    def test_season_must_be_int(self):
        """Ensure season doesn't silently accept a float with fractional part."""
        with pytest.raises(ValidationError):
            ResourceTitleParseResult.model_validate_json(
                json.dumps(
                    {
                        "anime_name": "Test",
                        "season": "one",
                        "episode": 1,
                        "quality": "1080p",
                        "fansub": None,
                        "languages": [],
                        "version": 1,
                    }
                )
            )

    # --- Serialization round-trip ---

    def test_json_round_trip(self):
        original = ResourceTitleParseResult(
            anime_name="Test",
            season=2,
            episode=10,
            quality=VideoQuality.Q2160P,
            fansub="Group",
            languages=[LanguageType.CHS, LanguageType.CHT],
            version=2,
            tmdb_id=999,
        )
        json_str = original.model_dump_json()
        restored = ResourceTitleParseResult.model_validate_json(json_str)
        assert original == restored
