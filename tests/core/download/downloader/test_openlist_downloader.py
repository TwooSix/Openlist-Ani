"""Tests for OpenListDownloader helper functions and init validation."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.download.downloader.api.model import (
    OpenlistTask,
    OpenlistTaskState,
)
from openlist_ani.core.download.downloader.base import HandlerStatus
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

SLEEP_PATCH_TARGET = (
    "openlist_ani.core.download.downloader.openlist_downloader.asyncio.sleep"
)


@pytest.fixture
def mock_async_sleep():
    with patch(SLEEP_PATCH_TARGET, new_callable=AsyncMock) as mock_sleep:
        yield mock_sleep


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_normal_name_unchanged(self):
        assert sanitize_filename("My Anime S01E01") == "My Anime S01E01"

    @pytest.mark.parametrize(
        ("raw_name", "forbidden"),
        [
            ("Re:Zero", ":"),
            ("What?", "?"),
            ("Star*Driver", "*"),
            ("A|B", "|"),
            ('He said "hi"', '"'),
            ("<SubGroup> Title", "<"),
            ("<SubGroup> Title", ">"),
        ],
    )
    def test_removes_invalid_characters(self, raw_name, forbidden):
        assert forbidden not in sanitize_filename(raw_name)

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

    @pytest.mark.parametrize(
        ("base_url", "offline_download_tool", "rename_format", "error_match"),
        [
            ("", "aria2", "{anime_name}", "base_url"),
            ("http://localhost", None, "{anime_name}", "offline_download_tool"),
            ("http://localhost", "aria2", None, "rename_format"),
        ],
    )
    def test_invalid_required_fields_raise(
        self,
        base_url,
        offline_download_tool,
        rename_format,
        error_match,
    ):
        with pytest.raises(ValueError, match=error_match):
            OpenListDownloader(
                base_url=base_url,
                token="tok",
                offline_download_tool=offline_download_tool,
                rename_format=rename_format,
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
# on_transferring – version suffix logic
# ---------------------------------------------------------------------------


def _make_downloader(
    rename_format="{anime_name} S{season:02d}E{episode:02d}", *, with_mock_client=True
):
    """Create an OpenListDownloader, optionally with a mocked client."""
    d = OpenListDownloader(
        base_url="http://localhost:5244",
        token="tok",
        offline_download_tool="aria2",
        rename_format=rename_format,
    )
    if with_mock_client:
        mock_client = AsyncMock()
        mock_client.mkdir = AsyncMock(return_value=True)
        mock_client.rename_file = AsyncMock(return_value=True)
        mock_client.move_file = AsyncMock(return_value=True)
        mock_client.remove_path = AsyncMock(return_value=True)
        d._client = mock_client
    return d


def _make_task(version=1, *, episode=3, quality=None, languages=None):
    """Create a DownloadTask in TRANSFERRING state."""
    info_kwargs = {
        "title": f"[SubGroup] MyAnime - {episode:02d} [1080p]",
        "download_url": "magnet:?xt=test",
        "anime_name": "MyAnime",
        "season": 1,
        "episode": episode,
        "version": version,
    }
    if quality is not None:
        info_kwargs["quality"] = quality
    if languages is not None:
        info_kwargs["languages"] = languages
    info = AnimeResourceInfo(**info_kwargs)
    task = DownloadTask(resource_info=info, save_path="/downloads")
    task.state = DownloadState.TRANSFERRING
    task.downloaded_filename = "something.mkv"
    task.temp_path = f"/downloads/{task.id}"
    return task


def _setup_download_done(client, task_id="dl-task-1"):
    """Configure mock client as if the offline download completed successfully."""
    client.get_offline_download_undone = AsyncMock(return_value=[])
    client.get_offline_download_done = AsyncMock(
        return_value=[
            OpenlistTask(
                id=task_id,
                name="download task",
                state=OpenlistTaskState.Succeeded,
            )
        ]
    )


def _assert_no_enum_repr(result: str):
    """Ensure no Python enum repr leaked into the formatted string."""
    assert "VideoQuality" not in result
    assert "LanguageType" not in result
    assert "<" not in result


# ---------------------------------------------------------------------------
# _detect_downloaded_file
# ---------------------------------------------------------------------------


class TestDetectDownloadedFile:
    """Verify recursive video detection and largest-file selection."""

    @pytest.mark.asyncio
    async def test_recursively_picks_largest_video(self):
        d = _make_downloader()
        task = _make_task()
        task.initial_files = []
        from types import SimpleNamespace

        d._client.list_files.side_effect = [
            [
                SimpleNamespace(name="readme.txt", is_dir=False, size=100),
                SimpleNamespace(name="small.mp4", is_dir=False, size=100),
                SimpleNamespace(name="batch", is_dir=True, size=0),
            ],
            [
                SimpleNamespace(name="ep01.mkv", is_dir=False, size=500),
                SimpleNamespace(name="ep02.mp4", is_dir=False, size=300),
            ],
        ]

        result = await d._detect_downloaded_file(task)
        assert result == "batch/ep01.mkv"

    @pytest.mark.asyncio
    async def test_returns_none_when_only_non_videos(self):
        d = _make_downloader()
        task = _make_task()
        task.initial_files = []
        from types import SimpleNamespace

        d._client.list_files.return_value = [
            SimpleNamespace(name="notes.txt", is_dir=False, size=10),
            SimpleNamespace(name="cover.jpg", is_dir=False, size=20),
        ]
        result = await d._detect_downloaded_file(task)
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_initial_files_and_chooses_next_largest(self):
        d = _make_downloader()
        task = _make_task()
        task.initial_files = ["batch/ep01.mkv"]
        from types import SimpleNamespace

        d._client.list_files.side_effect = [
            [
                SimpleNamespace(name="batch", is_dir=True, size=0),
                SimpleNamespace(name="movie.mp4", is_dir=False, size=300),
            ],
            [
                SimpleNamespace(name="ep01.mkv", is_dir=False, size=900),
                SimpleNamespace(name="ep02.mkv", is_dir=False, size=700),
            ],
        ]

        result = await d._detect_downloaded_file(task)
        assert result == "batch/ep02.mkv"


class TestTransferringVersionSuffix:
    """Test that version suffix is appended correctly during rename."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("version", "rename_format", "fansub", "expected_filename"),
        [
            (1, "{anime_name} S{season:02d}E{episode:02d}", None, "MyAnime S01E03.mkv"),
            (
                2,
                "{anime_name} S{season:02d}E{episode:02d}",
                None,
                "MyAnime S01E03 v2.mkv",
            ),
            (
                2,
                "{anime_name} S{season:02d}E{episode:02d} 1231231{version}",
                None,
                "MyAnime S01E03 v2.mkv",
            ),
            (
                2,
                "{anime_name} S{season:02d}E{episode:02d} [{fansub}]",
                "SubTeam",
                "MyAnime S01E03 [SubTeam] v2.mkv",
            ),
        ],
    )
    async def test_version_suffix_behaviors(
        self,
        version,
        rename_format,
        fansub,
        expected_filename,
        mock_async_sleep,
    ):
        d = _make_downloader(rename_format=rename_format)
        task = _make_task(version=version)
        if fansub is not None:
            task.resource_info.fansub = fansub

        result = await d.on_transferring(task)
        assert result.status == HandlerStatus.DONE

        new_filename = d._client.rename_file.call_args[0][1]
        assert new_filename == expected_filename
        if version == 1:
            assert "v1" not in new_filename


class TestLogProgressBucketed:
    def test_logs_once_per_25_percent_bucket(self):
        d = _make_downloader()
        task = _make_task()

        with patch(
            "openlist_ani.core.download.downloader.openlist_downloader.logger.info"
        ) as mock_info:
            for progress in [1, 10, 24, 25, 30, 49, 50, 74, 75, 90, 100]:
                d._log_progress(task, progress, is_transfer=False)

        assert mock_info.call_count == 4
        first_call_message = mock_info.call_args_list[0].args[0]
        assert "Downloading" in first_call_message
        last_call_message = mock_info.call_args_list[-1].args[0]
        assert "75%" in last_call_message

    def test_transfer_and_download_buckets_are_tracked_separately(self):
        d = _make_downloader()
        task = _make_task()

        with patch(
            "openlist_ani.core.download.downloader.openlist_downloader.logger.info"
        ) as mock_info:
            d._log_progress(task, 10, is_transfer=False)
            d._log_progress(task, 12, is_transfer=False)
            d._log_progress(task, 10, is_transfer=True)
            d._log_progress(task, 12, is_transfer=True)

        assert mock_info.call_count == 2


class TestOnDownloadingFlow:
    @pytest.mark.asyncio
    async def test_returns_poll_when_download_not_finished(self):
        d = _make_downloader()
        task = _make_task()
        task.extra_data["task_id"] = "dl-task-1"

        d._client.get_offline_download_undone = AsyncMock(
            return_value=[
                OpenlistTask(
                    id="dl-task-1",
                    name="download task",
                    progress=55,
                )
            ]
        )

        result = await d.on_downloading(task)
        assert result.status == HandlerStatus.POLL

    @pytest.mark.asyncio
    async def test_waits_when_matching_transfer_task_is_running(self):
        d = _make_downloader()
        task = _make_task()
        task.extra_data["task_id"] = "dl-task-1"

        _setup_download_done(d._client)
        d._client.get_offline_download_transfer_undone = AsyncMock(
            return_value=[
                OpenlistTask(
                    id="transfer-1",
                    name=f"transfer for uuid {task.id}",
                    state=OpenlistTaskState.Running,
                )
            ]
        )

        with patch.object(
            d,
            "_detect_downloaded_file",
            new_callable=AsyncMock,
            return_value="video.mkv",
        ) as mock_detect:
            result = await d.on_downloading(task)

        assert result.status == HandlerStatus.POLL
        mock_detect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_transfer_check_after_three_tries_and_succeeds(
        self, mock_async_sleep
    ):
        d = _make_downloader()
        task = _make_task()
        task.extra_data["task_id"] = "dl-task-1"

        _setup_download_done(d._client)
        d._client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
        d._client.get_offline_download_transfer_done = AsyncMock(return_value=[])

        with patch.object(
            d,
            "_detect_downloaded_file",
            new_callable=AsyncMock,
            return_value="video.mkv",
        ) as mock_detect:
            result = await d.on_downloading(task)

        assert result.status == HandlerStatus.DONE
        assert task.downloaded_filename == "video.mkv"
        assert d._client.get_offline_download_transfer_undone.await_count == 3
        assert d._client.get_offline_download_transfer_done.await_count == 3
        assert mock_async_sleep.await_count == 2
        mock_detect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_polls_when_file_not_found_after_download_complete(
        self, mock_async_sleep
    ):
        d = _make_downloader()
        task = _make_task()
        task.extra_data["task_id"] = "dl-task-1"

        _setup_download_done(d._client)
        d._client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
        d._client.get_offline_download_transfer_done = AsyncMock(return_value=[])

        with patch.object(
            d,
            "_detect_downloaded_file",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await d.on_downloading(task)

        assert result.status == HandlerStatus.POLL


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

    def test_quality_in_format_is_plain_string(self):
        """'{quality}' in rename_format must expand to '1080p', not the enum repr."""
        d = _make_downloader(
            "{anime_name} S{season:02d}E{episode:02d} {quality}",
            with_mock_client=False,
        )
        task = _make_task(episode=5, quality=VideoQuality.k1080p)
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05 1080p.mkv"
        _assert_no_enum_repr(result)

    @pytest.mark.parametrize(
        ("quality", "value_str"),
        [
            (VideoQuality.k2160p, "2160p"),
            (VideoQuality.k1080p, "1080p"),
            (VideoQuality.k720p, "720p"),
            (VideoQuality.k480p, "480p"),
            (VideoQuality.kUnknown, "unknown"),
        ],
    )
    def test_quality_all_variants_in_format(self, quality, value_str):
        """Every VideoQuality value should expand to its plain string value."""
        d = _make_downloader("{anime_name} [{quality}]", with_mock_client=False)
        task = _make_task(quality=quality)
        result = d._build_final_filename(task, "A", 1, 1)
        assert (
            f"[{value_str}]" in result
        ), f"Expected '[{value_str}]' in '{result}' for {quality!r}"
        _assert_no_enum_repr(result)

    def test_languages_in_format_is_joined_plain_string(self):
        """'{languages}' must expand to joined values like '简日', not a list repr."""
        d = _make_downloader(
            "{anime_name} S{season:02d}E{episode:02d} [{languages}]",
            with_mock_client=False,
        )
        task = _make_task(episode=5, languages=[LanguageType.kChs, LanguageType.kJp])
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05 [简日].mkv"
        _assert_no_enum_repr(result)

    @pytest.mark.parametrize(
        ("languages", "expected_contains", "expected_not_contains"),
        [
            ([LanguageType.kCht], "[繁]", None),
            ([LanguageType.kChs, LanguageType.kJp], None, "[]"),
        ],
    )
    def test_languages_variants(
        self,
        languages,
        expected_contains,
        expected_not_contains,
    ):
        d = _make_downloader("{anime_name} [{languages}]", with_mock_client=False)
        task = _make_task(languages=languages)
        result = d._build_final_filename(task, "Anime", 1, 1)
        if expected_contains:
            assert expected_contains in result
        if expected_not_contains:
            assert expected_not_contains not in result
        _assert_no_enum_repr(result)

    def test_quality_and_languages_combined_in_format(self):
        """Both fields together must both render as plain strings."""
        d = _make_downloader(
            "{anime_name} {quality} [{languages}]", with_mock_client=False
        )
        task = _make_task(
            quality=VideoQuality.k1080p,
            languages=[LanguageType.kChs, LanguageType.kCht],
        )
        result = d._build_final_filename(task, "MyAnime", 1, 3)
        assert result == "MyAnime 1080p [简繁].mkv"
        _assert_no_enum_repr(result)

    def test_no_quality_or_languages_in_format_still_works(self):
        """Default format without {quality} or {languages} must be unaffected."""
        d = _make_downloader(
            "{anime_name} S{season:02d}E{episode:02d}", with_mock_client=False
        )
        task = _make_task(
            episode=5,
            quality=VideoQuality.k1080p,
            languages=[LanguageType.kChs],
        )
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05.mkv"
