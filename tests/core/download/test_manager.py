"""Tests for DownloadManager — is_downloading, state persistence, callbacks, and dispatch."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openlist_ani.core.download.downloader.base import HandlerResult
from openlist_ani.core.download.manager import DownloadManager
from openlist_ani.core.download.model.task import DownloadState, DownloadTask
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
    """Create a mock downloader with all required handler stubs."""
    d = MagicMock()
    d.on_pending = AsyncMock(return_value=HandlerResult.done())
    d.on_downloading = AsyncMock(return_value=HandlerResult.done())
    d.on_transferring = AsyncMock(return_value=HandlerResult.done())
    d.on_cleaning_up = AsyncMock(return_value=HandlerResult.done())
    d.on_failed = AsyncMock()
    d.on_cancelled = AsyncMock()
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
        task = DownloadTask(resource_info=resource, save_path="/tmp")
        mgr._events["task1"] = task

        assert mgr.is_downloading(resource) is True

    def test_different_url_not_matching(self, tmp_path):
        mgr = DownloadManager(
            downloader=_make_mock_downloader(),
            state_file=str(tmp_path / "state.json"),
        )

        active = _make_resource(title="A", download_url="magnet:?xt=urn:btih:aaa")
        task = DownloadTask(resource_info=active, save_path="/tmp")
        mgr._events["task1"] = task

        query = _make_resource(title="B", download_url="magnet:?xt=urn:btih:bbb")
        assert mgr.is_downloading(query) is False

    def test_multiple_active_tasks(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "state.json")
        )
        for i in range(5):
            r = _make_resource(download_url=f"magnet:?xt=urn:btih:hash{i}")
            t = DownloadTask(resource_info=r, save_path="/dl")
            mgr._events[f"task{i}"] = t

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
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task
        mgr._save_state()

        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert task.id in data

    def test_load_skips_terminal_states(self, tmp_path):
        state_file = tmp_path / "state.json"
        downloader = _make_mock_downloader()

        # Write a completed task to state file
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        task.state = DownloadState.COMPLETED
        data = {task.id: task.to_dict()}
        state_file.write_text(json.dumps(data), encoding="utf-8")

        mgr = DownloadManager(downloader, state_file=str(state_file))
        assert task.id not in mgr._events

    def test_save_excludes_terminal_states(self, tmp_path):
        state_file = tmp_path / "state.json"
        downloader = _make_mock_downloader()

        mgr = DownloadManager(downloader, state_file=str(state_file))
        # Add a completed task
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        task.state = DownloadState.COMPLETED
        mgr._events[task.id] = task
        mgr._save_state()

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert task.id not in data

    def test_load_nonexistent_file(self, tmp_path):
        """Loading from a missing file should not crash."""
        state_file = tmp_path / "does_not_exist.json"
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(state_file))
        assert mgr._events == {}

    def test_load_corrupt_file(self, tmp_path):
        """Corrupt JSON should be handled gracefully, not crash."""
        state_file = tmp_path / "state.json"
        state_file.write_text("NOT VALID JSON!!!", encoding="utf-8")
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(state_file))
        assert mgr._events == {}

    def test_state_file_dir_created(self, tmp_path):
        """Parent directories should be created automatically."""
        state_file = tmp_path / "subdir" / "deep" / "state.json"
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(state_file))
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task
        mgr._save_state()
        assert state_file.exists()

    def test_init_with_pending_state_without_running_loop(self, tmp_path):
        """Init should not crash when recovered tasks exist but no running loop."""
        state_file = tmp_path / "state.json"
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        state_file.write_text(json.dumps({task.id: task.to_dict()}), encoding="utf-8")

        mgr = DownloadManager(_make_mock_downloader(), state_file=str(state_file))

        assert task.id in mgr._events
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
# get_event
# ---------------------------------------------------------------------------


class TestGetEvent:
    def test_existing_event(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "s.json")
        )
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events["id1"] = task
        assert mgr.get_event("id1") is task

    def test_missing_event_returns_none(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "s.json")
        )
        assert mgr.get_event("nonexistent") is None


# ---------------------------------------------------------------------------
# _run_state_machine and download flow (async)
# ---------------------------------------------------------------------------


class TestRunStateMachine:
    """Verify the state machine loop and error handling."""

    @pytest.mark.asyncio
    async def test_full_success_flow(self, tmp_path):
        """Task should transition through all states to COMPLETED."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)

        assert task.state == DownloadState.COMPLETED
        assert task.id not in mgr._events

    @pytest.mark.asyncio
    async def test_handler_failure_triggers_retry(self, tmp_path):
        """Failed handler should trigger retry logic."""
        downloader = _make_mock_downloader()
        call_count = 0

        def pending_with_retry(task):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return HandlerResult.fail("fail")
            return HandlerResult.done()

        downloader.on_pending = AsyncMock(side_effect=pending_with_retry)

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)
        assert task.state == DownloadState.COMPLETED

    @pytest.mark.asyncio
    async def test_same_state_polling_does_not_raise(self, tmp_path):
        """Polling in the same state should not trigger invalid self-transition."""
        downloader = _make_mock_downloader()
        downloader.on_downloading = AsyncMock(
            side_effect=[
                HandlerResult.poll(delay=0),
                HandlerResult.done(),
            ]
        )

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)

        assert task.state == DownloadState.COMPLETED
        assert downloader.on_downloading.await_count == 2

    @pytest.mark.asyncio
    async def test_handler_exception_marks_failed(self, tmp_path):
        """Unhandled exception in handler should mark task as failed, not crash."""
        downloader = _make_mock_downloader()
        downloader.on_pending = AsyncMock(side_effect=RuntimeError("boom"))

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = DownloadTask.from_resource_info(
            _make_resource(), save_path="/dl", max_retries=0
        )
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)
        assert task.state == DownloadState.FAILED

    @pytest.mark.asyncio
    async def test_on_complete_callback_called(self, tmp_path):
        """on_complete callback should fire when task completes."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        completed_tasks = []
        mgr.on_complete(lambda t: completed_tasks.append(t))

        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)
        assert len(completed_tasks) == 1
        assert completed_tasks[0].resource_info.title == "[SubGroup] Test - 01"

    @pytest.mark.asyncio
    async def test_on_error_callback_called(self, tmp_path):
        """on_error callback should fire on final failure."""
        downloader = _make_mock_downloader()
        downloader.on_pending = AsyncMock(return_value=HandlerResult.fail("fatal"))
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        errors = []
        mgr.on_error(lambda t, msg: errors.append((t, msg)))

        task = DownloadTask.from_resource_info(
            _make_resource(), save_path="/dl", max_retries=0
        )
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)
        assert len(errors) == 1
        assert "fatal" in errors[0][1]

    @pytest.mark.asyncio
    async def test_on_failed_hook_called_on_failure(self, tmp_path):
        """on_failed hook should be called when task fails."""
        downloader = _make_mock_downloader()
        downloader.on_pending = AsyncMock(return_value=HandlerResult.fail("err"))

        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))
        task = DownloadTask.from_resource_info(
            _make_resource(), save_path="/dl", max_retries=0
        )
        mgr._events[task.id] = task

        await mgr._run_state_machine(task)
        downloader.on_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_method(self, tmp_path):
        """DownloadManager.download should create task and process it."""
        downloader = _make_mock_downloader()
        mgr = DownloadManager(downloader, state_file=str(tmp_path / "state.json"))

        result = await mgr.download(_make_resource(), "/dl")
        assert result is True


# ---------------------------------------------------------------------------
# _finalize_task
# ---------------------------------------------------------------------------


class TestFinalizeTask:
    @pytest.mark.asyncio
    async def test_finalize_removes_from_events(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "state.json")
        )
        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task

        await mgr._finalize_task(task, success=True)
        assert task.id not in mgr._events

    @pytest.mark.asyncio
    async def test_finalize_calls_async_callback(self, tmp_path):
        mgr = DownloadManager(
            _make_mock_downloader(), state_file=str(tmp_path / "state.json")
        )
        results = []

        async def async_cb(t):
            results.append(t.id)

        mgr.on_complete(async_cb)

        task = DownloadTask.from_resource_info(_make_resource(), save_path="/dl")
        mgr._events[task.id] = task

        await mgr._finalize_task(task, success=True)
        assert len(results) == 1
