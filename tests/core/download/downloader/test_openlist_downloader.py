"""Tests for OpenListDownloader helper functions, init validation, and end-to-end download."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.download.api.model import (
    OpenlistTask,
    OpenlistTaskState,
)
from openlist_ani.core.download.downloader.base import DownloadError
from openlist_ani.core.download.downloader.openlist_downloader import (
    OpenListDownloader,
    format_anime_episode,
    sanitize_filename,
)
from openlist_ani.core.download.task import DownloadState, DownloadTask
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
# _transfer_to_final – version suffix logic
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
    """Create a DownloadTask in DOWNLOADING state."""
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
    task = DownloadTask(resource_info=info, base_path="/downloads")
    task.state = DownloadState.DOWNLOADING
    task.downloader_data["downloaded_filename"] = "something.mkv"
    task.downloader_data["temp_path"] = f"/downloads/{task.id}"
    return task


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

    async def test_recursively_picks_largest_video(self):
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["initial_files"] = []

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

    async def test_returns_none_when_only_non_videos(self, mock_async_sleep):
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["initial_files"] = []

        d._client.list_files.return_value = [
            SimpleNamespace(name="notes.txt", is_dir=False, size=10),
            SimpleNamespace(name="cover.jpg", is_dir=False, size=20),
        ]

        # Simulate time advancing past the timeout on second call
        start = 0.0
        with patch(
            "openlist_ani.core.download.downloader.openlist_downloader.time.monotonic",
            side_effect=[start, start + 31],
        ):
            result = await d._detect_downloaded_file(task)
        assert result is None

    async def test_ignores_initial_files_and_chooses_next_largest(self):
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["initial_files"] = ["batch/ep01.mkv"]

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


class TestTransferToFinal:
    """Test that version suffix is appended correctly during rename."""

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

        await d._transfer_to_final(task)

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


class TestWaitDownloadComplete:
    """Test _wait_download_complete polling behavior."""

    async def test_returns_when_download_succeeds(self, mock_async_sleep):
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["task_id"] = "dl-task-1"

        # First poll: still running, second poll: done
        d._client.get_offline_download_undone = AsyncMock(
            side_effect=[
                [
                    OpenlistTask(
                        id="dl-task-1",
                        name="download task",
                        progress=55,
                    )
                ],
                [],
            ]
        )
        d._client.get_offline_download_done = AsyncMock(
            return_value=[
                OpenlistTask(
                    id="dl-task-1",
                    name="download task",
                    state=OpenlistTaskState.SUCCEEDED,
                )
            ]
        )

        await d._wait_download_complete(task)
        assert mock_async_sleep.await_count == 1

    async def test_raises_when_task_not_found(self):
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["task_id"] = "dl-task-1"

        d._client.get_offline_download_undone = AsyncMock(return_value=[])
        d._client.get_offline_download_done = AsyncMock(return_value=[])

        with pytest.raises(DownloadError, match="not found"):
            await d._wait_download_complete(task)


class TestWaitTransferComplete:
    """Test _wait_transfer_complete polling and skip behavior."""

    async def test_waits_when_transfer_task_is_running(self, mock_async_sleep):
        d = _make_downloader()
        task = _make_task()

        # First poll: running, second poll: succeeded
        d._client.get_offline_download_transfer_undone = AsyncMock(
            side_effect=[
                [
                    OpenlistTask(
                        id="transfer-1",
                        name=f"transfer for uuid {task.id}",
                        state=OpenlistTaskState.RUNNING,
                    )
                ],
                [],
            ]
        )
        d._client.get_offline_download_transfer_done = AsyncMock(
            return_value=[
                OpenlistTask(
                    id="transfer-1",
                    name=f"transfer for uuid {task.id}",
                    state=OpenlistTaskState.SUCCEEDED,
                )
            ]
        )

        await d._wait_transfer_complete(task)
        assert mock_async_sleep.await_count == 1

    async def test_skips_after_max_retries_when_no_transfer_found(
        self, mock_async_sleep
    ):
        d = _make_downloader()
        task = _make_task()

        d._client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
        d._client.get_offline_download_transfer_done = AsyncMock(return_value=[])

        await d._wait_transfer_complete(task)
        assert d._client.get_offline_download_transfer_undone.await_count == 3
        assert d._client.get_offline_download_transfer_done.await_count == 3
        assert mock_async_sleep.await_count == 2


# ---------------------------------------------------------------------------
# _build_final_filename
# ---------------------------------------------------------------------------


class TestBuildFinalFilenameEnumFields:
    """Regression tests: quality and languages must be embedded as plain strings.

    Before the fix, (str, Enum) caused format() to produce repr-style output
    such as "<VideoQuality.Q1080P: '1080p'>" or
    "[<LanguageType.CHS: '简'>, <LanguageType.JP: '日'>]"
    instead of "1080p" / "简日".
    """

    def test_quality_in_format_is_plain_string(self):
        """'{quality}' in rename_format must expand to '1080p', not the enum repr."""
        d = _make_downloader(
            "{anime_name} S{season:02d}E{episode:02d} {quality}",
            with_mock_client=False,
        )
        task = _make_task(episode=5, quality=VideoQuality.Q1080P)
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05 1080p.mkv"
        _assert_no_enum_repr(result)

    @pytest.mark.parametrize(
        ("quality", "value_str"),
        [
            (VideoQuality.Q2160P, "2160p"),
            (VideoQuality.Q1080P, "1080p"),
            (VideoQuality.Q720P, "720p"),
            (VideoQuality.Q480P, "480p"),
            (VideoQuality.UNKNOWN, "unknown"),
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
        task = _make_task(episode=5, languages=[LanguageType.CHS, LanguageType.JP])
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05 [简日].mkv"
        _assert_no_enum_repr(result)

    @pytest.mark.parametrize(
        ("languages", "expected_contains", "expected_not_contains"),
        [
            ([LanguageType.CHT], "[繁]", None),
            ([LanguageType.CHS, LanguageType.JP], None, "[]"),
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
            quality=VideoQuality.Q1080P,
            languages=[LanguageType.CHS, LanguageType.CHT],
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
            quality=VideoQuality.Q1080P,
            languages=[LanguageType.CHS],
        )
        result = d._build_final_filename(task, "MyAnime", 1, 5)
        assert result == "MyAnime S01E05.mkv"


# ---------------------------------------------------------------------------
# _transfer_to_final – subdirectory file path handling
# ---------------------------------------------------------------------------


class TestTransferToFinalSubdirectoryPath:
    """Regression tests: files found in subdirectories of temp_path must be
    renamed and moved using the correct parent directory, not temp_path itself.

    Before the fix, a downloaded file at ``temp_path/batch/ep01.mkv`` would
    cause ``move_file(temp_path, …, ["MyAnime S01E03.mkv"])`` — the API
    would look for the file directly inside ``temp_path`` and fail because
    the file actually lived in ``temp_path/batch/``.
    """

    @pytest.mark.asyncio
    async def test_subdirectory_file_uses_correct_parent_for_rename(
        self, mock_async_sleep
    ):
        """rename_file should be called with the subdirectory path, not temp_path."""
        d = _make_downloader()
        task = _make_task()
        # Simulate a file detected inside a "batch" subdirectory
        task.downloader_data["downloaded_filename"] = "batch/ep03.mkv"
        task.downloader_data["temp_path"] = f"/downloads/{task.id}"

        # No conflict in destination
        d._client.list_files = AsyncMock(return_value=[])

        await d._transfer_to_final(task)

        # rename_file should be called with the full path inside the subdirectory
        rename_call_path = d._client.rename_file.call_args[0][0]
        assert rename_call_path == f"/downloads/{task.id}/batch/ep03.mkv"

    @pytest.mark.asyncio
    async def test_subdirectory_file_uses_correct_parent_for_move(
        self, mock_async_sleep
    ):
        """move_file src_dir should be the subdirectory, not temp_path."""
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["downloaded_filename"] = "batch/ep03.mkv"
        task.downloader_data["temp_path"] = f"/downloads/{task.id}"

        # No conflict in destination
        d._client.list_files = AsyncMock(return_value=[])

        await d._transfer_to_final(task)

        # move_file should use the subdirectory as src_dir
        move_call_args = d._client.move_file.call_args[0]
        src_dir = move_call_args[0]
        assert src_dir == f"/downloads/{task.id}/batch"

    @pytest.mark.asyncio
    async def test_non_subdirectory_file_uses_temp_path_directly(
        self, mock_async_sleep
    ):
        """A flat file (no subdirectory) should still use temp_path as src_dir."""
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["downloaded_filename"] = "ep03.mkv"
        task.downloader_data["temp_path"] = f"/downloads/{task.id}"

        # No conflict in destination
        d._client.list_files = AsyncMock(return_value=[])

        await d._transfer_to_final(task)

        move_call_args = d._client.move_file.call_args[0]
        src_dir = move_call_args[0]
        assert src_dir == f"/downloads/{task.id}"

    @pytest.mark.asyncio
    async def test_deeply_nested_subdirectory(self, mock_async_sleep):
        """Files in deeply nested subdirectories should resolve correctly."""
        d = _make_downloader()
        task = _make_task()
        task.downloader_data["downloaded_filename"] = "a/b/c/ep03.mkv"
        task.downloader_data["temp_path"] = f"/downloads/{task.id}"

        d._client.list_files = AsyncMock(return_value=[])

        await d._transfer_to_final(task)

        rename_call_path = d._client.rename_file.call_args[0][0]
        assert rename_call_path == f"/downloads/{task.id}/a/b/c/ep03.mkv"

        move_call_args = d._client.move_file.call_args[0]
        src_dir = move_call_args[0]
        assert src_dir == f"/downloads/{task.id}/a/b/c"


# ---------------------------------------------------------------------------
# End-to-end download() test
# ---------------------------------------------------------------------------


class TestOpenListDownloaderEndToEnd:
    """Exercise the full pipeline: submit → poll → transfer → detect → rename → complete."""

    async def test_full_download_lifecycle(self, mock_async_sleep):
        """Verify the complete download flow with mocked client calls."""
        d = _make_downloader(rename_format="{anime_name} S{season:02d}E{episode:02d}")
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[SubGroup] MyAnime - 03 [1080p]",
            download_url="magnet:?xt=test",
            anime_name="MyAnime",
            season=1,
            episode=3,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING

        # -- Step 1: _submit_download --
        # mkdir for temp dir
        mock_client.mkdir = AsyncMock(return_value=True)
        # list_files for initial files in temp dir (empty)
        mock_client.list_files = AsyncMock(return_value=[])
        # add_offline_download
        mock_client.add_offline_download = AsyncMock(
            return_value=[OpenlistTask(id="dl-task-1", name="offline dl")]
        )

        # -- Step 2: _wait_download_complete --
        # First poll: task running, second poll: not in undone, found in done
        mock_client.get_offline_download_undone = AsyncMock(
            side_effect=[
                [OpenlistTask(id="dl-task-1", name="dl", progress=50)],
                [],
            ]
        )
        mock_client.get_offline_download_done = AsyncMock(
            return_value=[
                OpenlistTask(
                    id="dl-task-1",
                    name="dl",
                    state=OpenlistTaskState.SUCCEEDED,
                )
            ]
        )

        # -- Step 3: _wait_transfer_complete --
        # No transfer task found after max retries — skip
        mock_client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
        mock_client.get_offline_download_transfer_done = AsyncMock(return_value=[])

        # -- Step 4: _detect_downloaded_file --
        # After transfer polling, list_files returns the downloaded file
        # We need to set list_files to return different things at different times:
        #   - First call: initial files listing (empty) during _submit_download
        #   - Subsequent calls: for _detect_downloaded_file
        #   - Then: for conflict resolution in _transfer_to_final
        list_files_calls = [
            # _submit_download initial listing
            [],
            # _detect_downloaded_file → _collect_video_files (temp dir)
            [SimpleNamespace(name="ep03.mkv", is_dir=False, size=500_000_000)],
            # _resolve_filename_conflict listing of final dir (empty = no conflict)
            [],
        ]
        mock_client.list_files = AsyncMock(side_effect=list_files_calls)

        # -- Step 5: _transfer_to_final --
        mock_client.rename_file = AsyncMock(return_value=True)
        mock_client.move_file = AsyncMock(return_value=True)

        # -- Cleanup --
        mock_client.remove_path = AsyncMock(return_value=True)

        # Execute the full lifecycle
        await d.download(task)

        # Verify task state
        assert task.output_path is not None
        assert "MyAnime" in task.output_path
        assert "Season 1" in task.output_path

        # Verify mkdir was called (temp dir + final dir)
        assert mock_client.mkdir.await_count >= 2

        # Verify add_offline_download was called with correct URL
        mock_client.add_offline_download.assert_awaited_once()
        call_kwargs = mock_client.add_offline_download.call_args
        assert call_kwargs.kwargs["urls"] == ["magnet:?xt=test"]

        # Verify move_file was called to move to final location
        mock_client.move_file.assert_awaited_once()

        # Verify cleanup was attempted
        mock_client.remove_path.assert_awaited_once()

    async def test_download_raises_on_mkdir_failure(self, mock_async_sleep):
        """Should raise DownloadError if initial mkdir fails."""
        d = _make_downloader()
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[Sub] Anime - 01",
            download_url="magnet:?xt=hash",
            anime_name="Anime",
            season=1,
            episode=1,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING

        mock_client.mkdir = AsyncMock(return_value=False)

        with pytest.raises(DownloadError, match="temporary directory"):
            await d.download(task)

    async def test_download_raises_on_offline_download_failure(self, mock_async_sleep):
        """Should raise DownloadError when add_offline_download returns None."""
        d = _make_downloader()
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[Sub] Anime - 01",
            download_url="magnet:?xt=hash",
            anime_name="Anime",
            season=1,
            episode=1,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING

        mock_client.mkdir = AsyncMock(return_value=True)
        mock_client.list_files = AsyncMock(return_value=[])
        mock_client.add_offline_download = AsyncMock(return_value=None)
        mock_client.remove_path = AsyncMock(return_value=True)

        with pytest.raises(DownloadError, match="Failed to create offline download"):
            await d.download(task)

    async def test_download_raises_on_file_detect_failure(self, mock_async_sleep):
        """Should raise DownloadError when no video file is detected."""
        d = _make_downloader()
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[Sub] Anime - 01",
            download_url="magnet:?xt=hash",
            anime_name="Anime",
            season=1,
            episode=1,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING

        # Submit succeeds
        mock_client.mkdir = AsyncMock(return_value=True)
        mock_client.list_files = AsyncMock(return_value=[])
        mock_client.add_offline_download = AsyncMock(
            return_value=[OpenlistTask(id="dl-1", name="dl")]
        )
        # Download completes immediately
        mock_client.get_offline_download_undone = AsyncMock(return_value=[])
        mock_client.get_offline_download_done = AsyncMock(
            return_value=[
                OpenlistTask(id="dl-1", name="dl", state=OpenlistTaskState.SUCCEEDED)
            ]
        )
        # No transfer task
        mock_client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
        mock_client.get_offline_download_transfer_done = AsyncMock(return_value=[])
        # Cleanup
        mock_client.remove_path = AsyncMock(return_value=True)

        # _detect_downloaded_file returns None (no video files, time out)
        start = 0.0
        with patch(
            "openlist_ani.core.download.downloader.openlist_downloader.time.monotonic",
            side_effect=[start, start + 31],
        ):
            with pytest.raises(DownloadError, match="Could not detect"):
                await d.download(task)

    async def test_download_idempotent_with_existing_task_id(self, mock_async_sleep):
        """If task already has a task_id, _submit_download should be a no-op."""
        d = _make_downloader()
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[Sub] Anime - 05",
            download_url="magnet:?xt=hash",
            anime_name="Anime",
            season=1,
            episode=5,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING
        task.downloader_data["task_id"] = "existing-task-id"
        task.downloader_data["temp_path"] = f"/downloads/{task.id}"
        task.downloader_data["initial_files"] = []

        # Download already complete
        mock_client.get_offline_download_undone = AsyncMock(return_value=[])
        mock_client.get_offline_download_done = AsyncMock(
            return_value=[
                OpenlistTask(
                    id="existing-task-id",
                    name="dl",
                    state=OpenlistTaskState.SUCCEEDED,
                )
            ]
        )
        # No transfer
        mock_client.get_offline_download_transfer_undone = AsyncMock(return_value=[])
        mock_client.get_offline_download_transfer_done = AsyncMock(return_value=[])
        # File detect
        mock_client.list_files = AsyncMock(
            side_effect=[
                # _detect_downloaded_file
                [SimpleNamespace(name="ep05.mkv", is_dir=False, size=1000)],
                # _resolve_filename_conflict
                [],
            ]
        )
        mock_client.rename_file = AsyncMock(return_value=True)
        mock_client.move_file = AsyncMock(return_value=True)
        mock_client.remove_path = AsyncMock(return_value=True)

        await d.download(task)

        # mkdir should NOT have been called for temp dir (idempotent skip)
        # It should only be called once for the final directory
        assert mock_client.mkdir.await_count == 1
        # add_offline_download should NOT have been called
        mock_client.add_offline_download.assert_not_awaited()

    async def test_cleanup_always_runs(self, mock_async_sleep):
        """Cleanup should run even when download fails."""
        d = _make_downloader()
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[Sub] Anime - 01",
            download_url="magnet:?xt=hash",
            anime_name="Anime",
            season=1,
            episode=1,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING

        # mkdir succeeds for temp, but add_offline_download fails
        mock_client.mkdir = AsyncMock(return_value=True)
        mock_client.list_files = AsyncMock(return_value=[])
        mock_client.add_offline_download = AsyncMock(return_value=None)
        mock_client.remove_path = AsyncMock(return_value=True)

        with pytest.raises(DownloadError):
            await d.download(task)

        # Cleanup should still be called because temp_path was set
        mock_client.remove_path.assert_awaited_once()

    async def test_download_failed_state_raises_download_error(self, mock_async_sleep):
        """Should raise DownloadError when the download task state is FAILED."""
        d = _make_downloader()
        mock_client = d._client

        info = AnimeResourceInfo(
            title="[Sub] Anime - 01",
            download_url="magnet:?xt=hash",
            anime_name="Anime",
            season=1,
            episode=1,
        )
        task = DownloadTask(resource_info=info, base_path="/downloads")
        task.state = DownloadState.DOWNLOADING

        mock_client.mkdir = AsyncMock(return_value=True)
        mock_client.list_files = AsyncMock(return_value=[])
        mock_client.add_offline_download = AsyncMock(
            return_value=[OpenlistTask(id="dl-1", name="dl")]
        )
        # Download finishes but with FAILED state
        mock_client.get_offline_download_undone = AsyncMock(return_value=[])
        mock_client.get_offline_download_done = AsyncMock(
            return_value=[
                OpenlistTask(id="dl-1", name="dl", state=OpenlistTaskState.FAILED)
            ]
        )
        mock_client.remove_path = AsyncMock(return_value=True)

        with pytest.raises(DownloadError, match="failed with state"):
            await d.download(task)
