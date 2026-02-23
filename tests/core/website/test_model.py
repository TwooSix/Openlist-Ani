"""Tests for AnimeResourceInfo data model."""

from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)


class TestAnimeResourceInfo:
    """Verify the data model behaves correctly with edge-case inputs."""

    def test_default_values(self):
        info = AnimeResourceInfo(title="test", download_url="magnet:?xt=urn:btih:abc")
        assert info.anime_name is None
        assert info.season is None
        assert info.episode is None
        assert info.fansub is None
        assert info.quality == VideoQuality.kUnknown
        assert info.languages == []
        assert info.version == 1

    def test_repr_with_none_fields(self):
        """repr() must not crash even when optional fields are None."""
        info = AnimeResourceInfo(title="t", download_url="d")
        result = repr(info)
        assert "title=" in result
        assert "anime_name=None" in result

    def test_languages_list_independence(self):
        """Each instance must have its own languages list (no shared mutable default)."""
        a = AnimeResourceInfo(title="a", download_url="u1")
        b = AnimeResourceInfo(title="b", download_url="u2")
        a.languages.append(LanguageType.kChs)
        assert b.languages == [], "Mutable default list leaked between instances"

    def test_empty_title(self):
        """Model should accept empty strings without crashing."""
        info = AnimeResourceInfo(title="", download_url="")
        assert info.title == ""
        assert info.download_url == ""


class TestEnumStringRepresentation:
    """Verify VideoQuality and LanguageType format as plain value strings.

    Regression tests for the bug where (str, Enum) was used instead of
    StrEnum, causing format() to produce repr-style output such as
    "<VideoQuality.k1080p: '1080p'>" instead of "1080p".
    """

    def test_video_quality_str_returns_value(self):
        """str() must return the bare value string, not the enum repr."""
        assert str(VideoQuality.k1080p) == "1080p"
        assert str(VideoQuality.k2160p) == "2160p"
        assert str(VideoQuality.k720p) == "720p"
        assert str(VideoQuality.kUnknown) == "unknown"

    def test_language_type_str_returns_value(self):
        """str() must return the bare value string, not the enum repr."""
        assert str(LanguageType.kChs) == "简"
        assert str(LanguageType.kCht) == "繁"
        assert str(LanguageType.kJp) == "日"
        assert str(LanguageType.kEng) == "英"
        assert str(LanguageType.kUnknown) == "未知"

    def test_video_quality_in_format_string(self):
        """f-string and .format() must embed the value, not the enum repr."""
        q = VideoQuality.k1080p
        assert f"{q}" == "1080p"
        assert "{q}".format(q=q) == "1080p"
        assert "quality={}".format(q) == "quality=1080p"

    def test_language_type_in_format_string(self):
        """f-string and .format() must embed the value, not the enum repr."""
        lang = LanguageType.kChs
        assert f"{lang}" == "简"
        assert "{lang}".format(lang=lang) == "简"

    def test_video_quality_equality_with_plain_string(self):
        """StrEnum instances must compare equal to plain value strings."""
        assert VideoQuality.k1080p == "1080p"
        assert VideoQuality.kUnknown == "unknown"

    def test_language_type_equality_with_plain_string(self):
        """StrEnum instances must compare equal to plain value strings."""
        assert LanguageType.kChs == "简"
        assert LanguageType.kJp == "日"
