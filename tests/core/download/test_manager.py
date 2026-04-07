"""Tests for DownloadManager — is_downloading, state persistence, callbacks, and dispatch."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openlist_ani.core.download.downloader.base import DownloadError
from openlist_ani.core.download.manager import DownloadManager
from openlist_ani.core.download.task import DownloadState, DownloadTask
from openlist_ani.core.website.model import AnimeResourceInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resource(**kwargs) -> AnimeResourceInfo:
    defaults = {
        "title": "[SubGroup] Test - 01",
        "download_url": "magnet:?xt=urn:btih:abc123",
        "anime_name": "Test",
        "season": 1,
        "episode": 1,
    }
    defaults.update(kwargs)
    return AnimeResourceInfo(**defaults)


def _make_mock_downloader():
    """Create a mock downloader with a successful download() stub."""
    d = MagicMock()
    d.download = AsyncMock()  # success by default (returns None)
    return d


# ---------------------------------------------------------------------------
# is_downloading
# ---------------------------------------------------------------------------


class TestIsDownloading:
    """Verify DownloadManager.is_downloading correctly identifies active tasks."""

    def test_resource_not_downloading(self, tmp_path):
        mgr = DownloadManager(
            downloader=_make_mock_downloader(),
            state_file=str(tmp_path / "state.json"),
        )
        resource = _make_resource()
        assert mgr.is_downloading(resource) is False

    def test_resource_is_downloading(self, tmp_path):
        mgr = DownloadManager(
            downloader=_make_mock_downloader(),
            state_file=str(tmp_path / "state.json"),
        )

        resource = _make_resource(
            title="Active",
            download_url="magnet:?xt=urn:btih:active",
        )
        task = DownloadTask(resource_info=resource, base_path=str(tmp_path))
        mgr._tasks["task1"] = task

        assert mgr.is_downloading(resource) is True

    def test_different_url_not_matching(self, tmp_path):
        mgr = DownloadManager(
            downloader=_make_mock_downloader(),
            state_file=str(tmp_path / "state.json"),
        )

        active = _make_resource(title="A", download_url="magnet:?xt=urn:btih:aaa")
        task = DownloadTask(resource_info=active, base_path=str(tmp_path))
        mgr._tasks["task1"] = task

        query = _make_resource(title="B", download_url="magnet:?xt=urn:btih:bbb")
        assert mgr.is_downloading(query) is False

    def test_multiple_active_tasks(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "state.json")
        )
        for i in range(5):
            r = _make_resource(download_url=f"magnet:?xt=urn:btih:hash{i}")
            t = DownloadTask(resource_info=r, base_path="/dl")
            mgr._tasks[f"task{i}"] = t

        query = _make_resource(download_url="magnet:?xt=urn:btih:hash3")
        assert mgr.is_downloading(query) is True

        query2 = _make_resource(download_url="magnet:?xt=urn:btih:notfound")
        assert mgr.is_downloading(query2) is False


# ---------------------------------------------------------------------------
# State file persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    """Verify tasks are correctly saved/loaded from the state file."""

    def test_save_and_load(self, tmp_path):
        state_file = tmp_path / "state.json"
        downloader = _make_mock_downloader()

        mgr = DownloadManager(downloader, state_file=str(state_file))
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task
        mgr._save_state()

        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert task.id in data

    def test_load_skips_terminal_states(self, tmp_path):
        state_file = tmp_path / "state.json"
        downloader = _make_mock_downloader()

        # Write a completed task to state file
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        task.state = DownloadState.COMPLETED
        data = {task.id: task.to_dict()}
        state_file.write_text(json.dumps(data), encoding="utf-8")

        mgr = DownloadManager(downloader, state_file=str(state_file))
        assert task.id not in mgr._tasks

    def test_save_excludes_terminal_states(self, tmp_path):
        state_file = tmp_path / "state.json"
        downloader = _make_mock_downloader()

        mgr = DownloadManager(downloader, state_file=str(state_file))
        # Add a completed task
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        task.state = DownloadState.COMPLETED
        mgr._tasks[task.id] = task
        mgr._save_state()

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert task.id not in data

    def test_load_nonexistent_file(self, tmp_path):
        """Loading from a missing file should not crash."""
        state_file = tmp_path / "does_not_exist.json"
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(state_file))
        assert mgr._tasks == {}

    def test_load_corrupt_file(self, tmp_path):
        """Corrupt JSON should be handled gracefully, not crash."""
        state_file = tmp_path / "state.json"
        state_file.write_text("NOT VALID JSON!!!", encoding="utf-8")
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(state_file))
        assert mgr._tasks == {}

    def test_state_file_dir_created(self, tmp_path):
        """Parent directories should be created automatically."""
        state_file = tmp_path / "subdir" / "deep" / "state.json"
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(state_file))
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task
        mgr._save_state()
        assert state_file.exists()

    def test_init_with_pending_state_without_running_loop(self, tmp_path):
        """Init should not crash when recovered tasks exist but no running loop."""
        state_file = tmp_path / "state.json"
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        state_file.write_text(json.dumps({task.id: task.to_dict()}), encoding="utf-8")

        mgr = DownloadManager(_make_mock_downloader(), state_file=str(state_file))

        assert task.id in mgr._tasks
        assert mgr._background_tasks == set()


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    """Verify on_complete and on_error callback registration."""

    def test_register_on_complete(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "s.json")
        )
        cb = MagicMock()
        mgr.on_complete(cb)
        assert cb in mgr._on_complete

    def test_register_on_error(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "s.json")
        )
        cb = MagicMock()
        mgr.on_error(cb)
        assert cb in mgr._on_error


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    def test_existing_task(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "s.json")
        )
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks["id1"] = task
        assert mgr.get_task("id1") is task

    def test_missing_task_returns_none(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "s.json")
        )
        assert mgr.get_task("nonexistent") is None


# ---------------------------------------------------------------------------
# _run_state_machine and download flow (async)
# ---------------------------------------------------------------------------


class TestRunDownload:
    """Verify the download lifecycle and error handling."""

    @pytest.mark.asyncio
    async def test_full_success_flow(self, tmp_path):
        """Task should transition PENDING → DOWNLOADING → COMPLETED."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task

        await mgr._run_download(task)

        assert task.state == DownloadState.COMPLETED
        assert task.id not in mgr._tasks

    @pytest.mark.asyncio
    async def test_download_error_triggers_retry(self, tmp_path):
        """DownloadError should trigger retry logic."""
        downloader = _make_mock_downloader()
        call_count = 0

        def download_with_retry(task):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise DownloadError("fail")

        downloader.download = AsyncMock(side_effect=download_with_retry)

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task

        await mgr._run_download(task)
        assert task.state == DownloadState.COMPLETED

    @pytest.mark.asyncio
    async def test_unexpected_exception_marks_failed(self, tmp_path):
        """Unhandled exception in download() should mark task as failed."""
        downloader = _make_mock_downloader()
        downloader.download = AsyncMock(side_effect=RuntimeError("boom"))

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = DownloadTask.from_resource_info(
            _make_resource(), base_path="/dl", max_retries=0
        )
        mgr._tasks[task.id] = task

        await mgr._run_download(task)
        assert task.state == DownloadState.FAILED

    @pytest.mark.asyncio
    async def test_on_complete_callback_called(self, tmp_path):
        """on_complete callback should fire when task completes."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        completed_tasks = []
        mgr.on_complete(lambda t: completed_tasks.append(t))

        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task

        await mgr._run_download(task)
        assert len(completed_tasks) == 1
        assert completed_tasks[0].resource_info.title == "[SubGroup] Test - 01"

    @pytest.mark.asyncio
    async def test_on_error_callback_called(self, tmp_path):
        """on_error callback should fire on final failure."""
        downloader = _make_mock_downloader()
        downloader.download = AsyncMock(side_effect=DownloadError("fatal"))
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        errors = []
        mgr.on_error(lambda t, msg: errors.append((t, msg)))

        task = DownloadTask.from_resource_info(
            _make_resource(), base_path="/dl", max_retries=0
        )
        mgr._tasks[task.id] = task

        await mgr._run_download(task)
        assert len(errors) == 1
        assert "fatal" in errors[0][1]

    @pytest.mark.asyncio
    async def test_download_method(self, tmp_path):
        """DownloadManager.download should create task and process it."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        result = await mgr.download(_make_resource(), "/dl")
        assert result is True

    @pytest.mark.asyncio
    async def test_recovered_downloading_task_resumes(self, tmp_path):
        """A task in DOWNLOADING state should be directly passed to download()."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        task.state = DownloadState.DOWNLOADING
        mgr._tasks[task.id] = task

        await mgr._run_download(task)
        assert task.state == DownloadState.COMPLETED
        downloader.download.assert_awaited_once()


# ---------------------------------------------------------------------------
# _finalize_task
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# submit (non-blocking)
# ---------------------------------------------------------------------------


class TestSubmit:
    """Verify DownloadManager.submit() creates background tasks correctly."""

    async def test_submit_returns_task_immediately(self, tmp_path):
        """submit() should return a PENDING task without blocking."""
        downloader = _make_mock_downloader()
        download_started = asyncio.Event()
        download_gate = asyncio.Event()

        async def slow_download(task):
            download_started.set()
            await download_gate.wait()

        downloader.download = AsyncMock(side_effect=slow_download)

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = await mgr.submit(_make_resource(), str(tmp_path))

        # submit returns immediately — task is PENDING or just started DOWNLOADING
        assert task.state in (DownloadState.PENDING, DownloadState.DOWNLOADING)
        assert isinstance(task, DownloadTask)

        # Unblock background task to avoid warnings
        download_gate.set()
        await download_started.wait()
        await asyncio.sleep(0.05)

    async def test_submit_registers_task(self, tmp_path):
        """Submitted task must appear in mgr._tasks."""
        downloader = _make_mock_downloader()
        download_gate = asyncio.Event()

        async def slow_download(task):
            await download_gate.wait()

        downloader.download = AsyncMock(side_effect=slow_download)

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = await mgr.submit(_make_resource(), str(tmp_path))

        assert task.id in mgr._tasks
        assert mgr._tasks[task.id] is task

        download_gate.set()
        await asyncio.sleep(0.05)

    async def test_submit_persists_state(self, tmp_path):
        """State file should be written after submit."""
        state_file = tmp_path / "state.json"
        downloader = _make_mock_downloader()
        download_gate = asyncio.Event()

        async def slow_download(task):
            await download_gate.wait()

        downloader.download = AsyncMock(side_effect=slow_download)

        mgr = DownloadManager(downloader, state_file=str(state_file))
        task = await mgr.submit(_make_resource(), str(tmp_path))

        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert task.id in data

        download_gate.set()
        await asyncio.sleep(0.05)

    async def test_submit_background_task_completes(self, tmp_path):
        """Background task should eventually reach COMPLETED."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        task = await mgr.submit(_make_resource(), str(tmp_path))

        # Wait for background task to finish
        for _ in range(50):
            if task.state == DownloadState.COMPLETED:
                break
            await asyncio.sleep(0.01)

        assert task.state == DownloadState.COMPLETED


# ---------------------------------------------------------------------------
# download method
# ---------------------------------------------------------------------------


class TestDownload:
    """Test DownloadManager.download() end-to-end behavior."""

    @pytest.mark.asyncio
    async def test_download_success_path(self, tmp_path):
        """Successful download should return True."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        resource = _make_resource()

        result = await mgr.download(resource, "/downloads")

        assert result is True
        downloader.download.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_failure_path(self, tmp_path):
        """Failed download should return False."""
        downloader = _make_mock_downloader()
        downloader.download.side_effect = DownloadError("Download failed")
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        resource = _make_resource()

        result = await mgr.download(resource, "/downloads")

        assert result is False

    @pytest.mark.asyncio
    async def test_download_state_transitions(self, tmp_path):
        """Task should transition PENDING → DOWNLOADING → COMPLETED."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        resource = _make_resource()

        # Track state changes during download
        states_seen = []

        def track_download(task):
            states_seen.append(task.state.value)
            # Verify we're in DOWNLOADING state when download() is called
            assert task.state == DownloadState.DOWNLOADING
            return None

        downloader.download = AsyncMock(side_effect=track_download)

        # Create task manually to track states
        task = DownloadTask.from_resource_info(resource, base_path="/downloads")
        mgr._tasks[task.id] = task

        # Initially PENDING
        states_seen.append(task.state.value)

        await mgr._run_download(task)

        # Final state
        states_seen.append(task.state.value)

        assert states_seen == ["pending", "downloading", "completed"]


# ---------------------------------------------------------------------------
# _finalize_task
# ---------------------------------------------------------------------------


class TestFinalizeTask:
    @pytest.mark.asyncio
    async def test_finalize_removes_from_tasks(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "state.json")
        )
        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task

        await mgr._finalize_task(task, success=True)
        assert task.id not in mgr._tasks

    @pytest.mark.asyncio
    async def test_finalize_calls_async_callback(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "state.json")
        )
        results = []

        async def async_cb(t):
            await asyncio.sleep(0)
            results.append(t.id)

        mgr.on_complete(async_cb)

        task = DownloadTask.from_resource_info(_make_resource(), base_path="/dl")
        mgr._tasks[task.id] = task

        await mgr._finalize_task(task, success=True)
        assert len(results) == 1
