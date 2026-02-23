"""Tests for OpenListDownloader helper functions and init validation."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.download.downloader.openlist_downloader import (
    OpenListDownloader,
    format_anime_episode,
    sanitize_filename,
)
from openlist_ani.core.download.model.task import DownloadState, DownloadTask
from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)

# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_normal_name_unchanged(self):
        assert sanitize_filename("My Anime S01E01") == "My Anime S01E01"

    def test_removes_colon(self):
        assert ":" not in sanitize_filename("Re:Zero")

    def test_removes_question_mark(self):
        assert "?" not in sanitize_filename("What?")

    def test_removes_star(self):
        assert "*" not in sanitize_filename("Star*Driver")

    def test_removes_angle_brackets(self):
        result = sanitize_filename("<SubGroup> Title")
        assert "<" not in result
        assert ">" not in result

    def test_removes_pipe(self):
        assert "|" not in sanitize_filename("A|B")

    def test_removes_quotes(self):
        assert '"' not in sanitize_filename('He said "hi"')

    def test_strips_whitespace(self):
        result = sanitize_filename("  name  ")
        assert result == "name"

    def test_empty_string(self):
        result = sanitize_filename("")
        assert result == ""

    def test_all_invalid_chars(self):
        """A string of only invalid chars becomes spaces then stripped."""
        result = sanitize_filename('<>:"/\\|?*')
        assert result.strip() == result  # no leading/trailing whitespace


# ---------------------------------------------------------------------------
# format_anime_episode
# ---------------------------------------------------------------------------


class TestFormatAnimeEpisode:
    def test_normal(self):
        assert format_anime_episode("Bocchi", 1, 3) == "Bocchi S01E03"

    def test_none_name(self):
        result = format_anime_episode(None, 1, 1)
        assert "Unknown" in result

    def test_none_season(self):
        result = format_anime_episode("A", None, 5)
        assert "S??" in result
        assert "E05" in result

    def test_none_episode(self):
        result = format_anime_episode("A", 2, None)
        assert "S02" in result
        assert "E??" in result

    def test_all_none(self):
        result = format_anime_episode(None, None, None)
        assert result == "Unknown S??E??"


# ---------------------------------------------------------------------------
# OpenListDownloader.__init__ validation
# ---------------------------------------------------------------------------


class TestOpenListDownloaderInit:
    """Ensure constructor validates required parameters to prevent coredump-like issues."""

    def test_empty_base_url_raises(self):
        with pytest.raises(ValueError, match="base_url"):
            OpenListDownloader(
                base_url="",
                token="tok",
                offline_download_tool="aria2",
                rename_format="{anime_name}",
            )

    def test_none_offline_tool_raises(self):
        with pytest.raises(ValueError, match="offline_download_tool"):
            OpenListDownloader(
                base_url="http://localhost",
                token="tok",
                offline_download_tool=None,
                rename_format="{anime_name}",
            )

    def test_none_rename_format_raises(self):
        with pytest.raises(ValueError, match="rename_format"):
            OpenListDownloader(
                base_url="http://localhost",
                token="tok",
                offline_download_tool="aria2",
                rename_format=None,
            )

    def test_valid_init(self):
        d = OpenListDownloader(
            base_url="http://localhost:5244",
            token="t",
            offline_download_tool="aria2",
            rename_format="{anime_name} S{season:02d}E{episode:02d}",
        )
        assert d.downloader_type == "openlist"

    def test_lazy_client_creation(self):
        """Client should not be created until first access."""
        d = OpenListDownloader(
            base_url="http://localhost:5244",
            token="tok",
            offline_download_tool="aria2",
            rename_format="{anime_name}",
        )
        assert d._client is None
        client = d.client
        assert client is not None
        # Second access returns same instance
        assert d.client is client


# ---------------------------------------------------------------------------
# handle_downloaded – version suffix logic
# ---------------------------------------------------------------------------


def _make_downloader(rename_format="{anime_name} S{season:02d}E{episode:02d}"):
    """Create an OpenListDownloader with a mocked client."""
    d = OpenListDownloader(
        base_url="http://localhost:5244",
        token="tok",
        offline_download_tool="aria2",
        rename_format=rename_format,
    )
    mock_client = AsyncMock()
    mock_client.mkdir = AsyncMock(return_value=True)
    mock_client.rename_file = AsyncMock(return_value=True)
    mock_client.move_file = AsyncMock(return_value=True)
    mock_client.remove_path = AsyncMock(return_value=True)
    d._client = mock_client
    return d


def _make_task(version=1):
    """Create a DownloadTask in DOWNLOADED state with given version."""
    info = AnimeResourceInfo(
        title="[SubGroup] MyAnime - 03 [1080p]",
        download_url="magnet:?xt=test",
        anime_name="MyAnime",
        season=1,
        episode=3,
        version=version,
    )
    task = DownloadTask(
        resource_info=info,
        save_path="/downloads",
    )
    task.state = DownloadState.DOWNLOADED
    task.downloaded_filename = "something.mkv"
    task.temp_path = f"/downloads/{task.id}"
    return task


class TestHandleDownloadedVersionSuffix:
    """Test that version suffix is appended correctly during rename."""

    @pytest.mark.asyncio
    @patch(
        "openlist_ani.core.download.downloader.openlist_downloader.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_version_1_no_suffix(self, _mock_sleep):
        """version=1 should NOT add any version suffix."""
        d = _make_downloader()
        task = _make_task(version=1)
        result = await d.handle_downloaded(task)
        assert result.success

        # The rename call should use a filename without 'v1'
        rename_call_args = d._client.rename_file.call_args
        new_filename = rename_call_args[0][1] if rename_call_args else None
        if new_filename:
            assert "v1" not in new_filename
            assert new_filename == "MyAnime S01E03.mkv"

    @pytest.mark.asyncio
    @patch(
        "openlist_ani.core.download.downloader.openlist_downloader.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_version_2_appends_v2(self, _mock_sleep):
        """version=2 should append ' v2' to the filename stem."""
        d = _make_downloader()
        task = _make_task(version=2)
        result = await d.handle_downloaded(task)
        assert result.success

        rename_call_args = d._client.rename_file.call_args
        new_filename = rename_call_args[0][1]
        assert new_filename == "MyAnime S01E03 v2.mkv"

    @pytest.mark.asyncio
    @patch(
        "openlist_ani.core.download.downloader.openlist_downloader.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_version_not_in_format_context(self, _mock_sleep):
        """Format string containing {version} always falls back because version is removed from context."""
        d = _make_downloader(
            rename_format="{anime_name} S{season:02d}E{episode:02d} 1231231{version}"
        )
        task = _make_task(version=2)
        result = await d.handle_downloaded(task)
        assert result.success

        # format fails because version was removed from context → fallback used
        rename_call_args = d._client.rename_file.call_args
        new_filename = rename_call_args[0][1]
        # Fallback: "MyAnime S01E03" + " v2" + ".mkv"
        assert new_filename == "MyAnime S01E03 v2.mkv"

    @pytest.mark.asyncio
    @patch(
        "openlist_ani.core.download.downloader.openlist_downloader.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_custom_format_with_fansub(self, _mock_sleep):
        """Custom format using {fansub} (without {version}) should work and still append version suffix."""
        d = _make_downloader(
            rename_format="{anime_name} S{season:02d}E{episode:02d} [{fansub}]"
        )
        task = _make_task(version=2)
        task.resource_info.fansub = "SubTeam"
        result = await d.handle_downloaded(task)
        assert result.success

        rename_call_args = d._client.rename_file.call_args
        new_filename = rename_call_args[0][1]
        assert new_filename == "MyAnime S01E03 [SubTeam] v2.mkv"


# ---------------------------------------------------------------------------
# _build_final_filename
# ---------------------------------------------------------------------------


class TestBuildFinalFilenameEnumFields:
    """Regression tests: quality and languages must be embedded as plain strings.

    Before the fix, (str, Enum) caused format() to produce repr-style output
    such as "<VideoQuality.k1080p: '1080p'>" or
    "[<LanguageType.kChs: '简'>, <LanguageType.kJp: '日'>]"
    instead of "1080p" / "简日".
    """

    def _make_task_with_enums(
        self,
        quality: VideoQuality = VideoQuality.k1080p,
        languages: list = None,
        version: int = 1,
    ) -> DownloadTask:
        info = AnimeResourceInfo(
            title="[Sub] Anime - 05 [1080p]",
            download_url="magnet:?xt=test",
            anime_name="MyAnime",
            season=1,
            episode=5,
            quality=quality,
            languages=languages or [LanguageType.kChs, LanguageType.kJp],
            version=version,
        )
        task = DownloadTask(resource_info=info, save_path="/downloads")
        task.state = DownloadState.DOWNLOADED
        task.downloaded_filename = "source.mkv"
        task.temp_path = f"/downloads/{task.id}"
        return task

    def _make_downloader_direct(self, rename_format: str) -> OpenListDownloader:
        d = OpenListDownloader(
            base_url="http://localhost:5244",
            token="tok",
            offline_download_tool="aria2",
            rename_format=rename_format,
        )
        return d

    def test_quality_in_format_is_plain_string(self):
        """'{quality}' in rename_format must expand to '1080p', not the enum repr."""
        d = self._make_downloader_direct(
            "{anime_name} S{season:02d}E{episode:02d} {quality}"
        )
        task = self._make_task_with_enums(quality=VideoQuality.k1080p)
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05 1080p.mkv"
        assert "VideoQuality" not in result
        assert "<" not in result

    def test_quality_all_variants_in_format(self):
        """Every VideoQuality value should expand to its plain string value."""
        expected = {
            VideoQuality.k2160p: "2160p",
            VideoQuality.k1080p: "1080p",
            VideoQuality.k720p: "720p",
            VideoQuality.k480p: "480p",
            VideoQuality.kUnknown: "unknown",
        }
        d = self._make_downloader_direct("{anime_name} [{quality}]")
        for quality, value_str in expected.items():
            task = self._make_task_with_enums(quality=quality)
            result = d._build_final_filename(task, "A", 1, 1)
            assert (
                f"[{value_str}]" in result
            ), f"Expected '[{value_str}]' in '{result}' for {quality!r}"

    def test_languages_in_format_is_joined_plain_string(self):
        """'{languages}' must expand to joined values like '简日', not a list repr."""
        d = self._make_downloader_direct(
            "{anime_name} S{season:02d}E{episode:02d} [{languages}]"
        )
        task = self._make_task_with_enums(
            languages=[LanguageType.kChs, LanguageType.kJp]
        )
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05 [简日].mkv"
        assert "LanguageType" not in result
        assert "<" not in result

    def test_languages_single_entry(self):
        """A single-language list must expand to that language's value string."""
        d = self._make_downloader_direct("{anime_name} [{languages}]")
        task = self._make_task_with_enums(languages=[LanguageType.kCht])
        result = d._build_final_filename(task, "Anime", 1, 1)
        assert "[繁]" in result

    def test_languages_empty_list(self):
        """Empty languages list must expand to empty string without crashing."""
        d = self._make_downloader_direct("{anime_name} [{languages}]")
        task = self._make_task_with_enums(languages=[])
        result = d._build_final_filename(task, "Anime", 1, 1)
        assert "[]" not in result  # should be "[]".format(...) → "[]"
        assert "LanguageType" not in result

    def test_quality_and_languages_combined_in_format(self):
        """Both fields together must both render as plain strings."""
        d = self._make_downloader_direct("{anime_name} {quality} [{languages}]")
        task = self._make_task_with_enums(
            quality=VideoQuality.k1080p,
            languages=[LanguageType.kChs, LanguageType.kCht],
        )
        result = d._build_final_filename(task, "MyAnime", 1, 3)
        assert result == "MyAnime 1080p [简繁].mkv"

    def test_no_quality_or_languages_in_format_still_works(self):
        """Default format without {quality} or {languages} must be unaffected."""
        d = self._make_downloader_direct("{anime_name} S{season:02d}E{episode:02d}")
        task = self._make_task_with_enums(
            quality=VideoQuality.k1080p,
            languages=[LanguageType.kChs],
        )
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05.mkv"
