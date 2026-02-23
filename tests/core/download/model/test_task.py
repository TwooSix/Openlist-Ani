"""Tests for DownloadTask state machine and serialization."""

import json

import pytest

from openlist_ani.core.download.model.task import (
    STATE_TRANSITIONS,
    DownloadState,
    DownloadTask,
    InvalidStateTransitionError,
)
from openlist_ani.core.website.model import (
    AnimeResourceInfo,
    LanguageType,
    VideoQuality,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resource(**kwargs) -> AnimeResourceInfo:
    defaults = {
        "title": "[SubGroup] Anime Name - 01 [1080p]",
        "download_url": "magnet:?xt=urn:btih:abc123",
        "anime_name": "Anime Name",
        "season": 1,
        "episode": 1,
    }
    defaults.update(kwargs)
    return AnimeResourceInfo(**defaults)


def _make_task(**kwargs) -> DownloadTask:
    defaults = {
        "resource_info": _make_resource(),
        "save_path": "/downloads",
    }
    defaults.update(kwargs)
    return DownloadTask(**defaults)


# ---------------------------------------------------------------------------
# Construction & defaults
# ---------------------------------------------------------------------------


class TestDownloadTaskCreation:
    """Verify task creation and sensible default values."""

    def test_default_state_is_pending(self):
        task = _make_task()
        assert task.state == DownloadState.PENDING

    def test_id_auto_generated(self):
        t1 = _make_task()
        t2 = _make_task()
        assert t1.id != t2.id
        assert len(t1.id) > 0

    def test_retry_defaults(self):
        task = _make_task()
        assert task.retry_count == 0
        assert task.max_retries == 3

    def test_timestamps_populated(self):
        task = _make_task()
        assert task.created_at is not None
        assert task.updated_at is not None

    def test_from_resource_info(self):
        res = _make_resource(title="Test", download_url="magnet:?test")
        task = DownloadTask.from_resource_info(res, save_path="/anime")
        assert task.resource_info.title == "Test"
        assert task.save_path == "/anime"
        assert task.state == DownloadState.PENDING

    def test_optional_fields_none(self):
        task = _make_task()
        assert task.temp_path is None
        assert task.final_path is None
        assert task.downloaded_filename is None
        assert task.started_at is None
        assert task.completed_at is None
        assert task.error_message is None


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    """Verify the download state machine enforces valid transitions."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (DownloadState.PENDING, DownloadState.DOWNLOADING),
            (DownloadState.PENDING, DownloadState.CANCELLED),
            (DownloadState.PENDING, DownloadState.FAILED),
            (DownloadState.DOWNLOADING, DownloadState.TRANSFERRING),
            (DownloadState.DOWNLOADING, DownloadState.FAILED),
            (DownloadState.DOWNLOADING, DownloadState.CANCELLED),
            (DownloadState.TRANSFERRING, DownloadState.CLEANING_UP),
            (DownloadState.TRANSFERRING, DownloadState.FAILED),
            (DownloadState.CLEANING_UP, DownloadState.COMPLETED),
            (DownloadState.CLEANING_UP, DownloadState.FAILED),
            (DownloadState.FAILED, DownloadState.PENDING),
        ],
    )
    def test_valid_transitions(self, from_state, to_state):
        task = _make_task()
        task.state = from_state
        task.update_state(to_state)
        assert task.state == to_state

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (DownloadState.PENDING, DownloadState.COMPLETED),
            (DownloadState.PENDING, DownloadState.CLEANING_UP),
            (DownloadState.DOWNLOADING, DownloadState.PENDING),
            (DownloadState.DOWNLOADING, DownloadState.COMPLETED),
            (DownloadState.COMPLETED, DownloadState.PENDING),
            (DownloadState.COMPLETED, DownloadState.DOWNLOADING),
            (DownloadState.CANCELLED, DownloadState.DOWNLOADING),
        ],
    )
    def test_invalid_transitions_raise(self, from_state, to_state):
        task = _make_task()
        task.state = from_state
        with pytest.raises(InvalidStateTransitionError):
            task.update_state(to_state)

    def test_completed_is_terminal(self):
        """COMPLETED state has no valid outgoing transitions."""
        assert STATE_TRANSITIONS[DownloadState.COMPLETED] == set()

    def test_update_state_refreshes_timestamp(self):
        task = _make_task()
        old_ts = task.updated_at
        task.update_state(DownloadState.DOWNLOADING)
        assert task.updated_at >= old_ts


# ---------------------------------------------------------------------------
# mark_failed / retry
# ---------------------------------------------------------------------------


class TestFailureAndRetry:
    """Verify failure marking and retry mechanics."""

    def test_mark_failed(self):
        task = _make_task()
        task.update_state(DownloadState.DOWNLOADING)
        task.mark_failed("network error")
        assert task.state == DownloadState.FAILED
        assert task.error_message == "network error"

    def test_can_retry_when_failed(self):
        task = _make_task()
        task.update_state(DownloadState.DOWNLOADING)
        task.mark_failed("err")
        assert task.can_retry() is True

    def test_cannot_retry_when_not_failed(self):
        task = _make_task()
        assert task.can_retry() is False

    def test_retry_resets_state(self):
        task = _make_task()
        task.update_state(DownloadState.DOWNLOADING)
        task.mark_failed("err")
        task.retry()
        assert task.state == DownloadState.PENDING
        assert task.retry_count == 1
        assert task.error_message is None

    def test_max_retries_exhausted(self):
        task = _make_task(max_retries=2)
        for _ in range(2):
            task.update_state(DownloadState.DOWNLOADING)
            task.mark_failed("err")
            task.retry()
        # Now fail again – should not be retryable
        task.update_state(DownloadState.DOWNLOADING)
        task.mark_failed("final err")
        assert task.can_retry() is False

    def test_retry_raises_when_exhausted(self):
        task = _make_task(max_retries=0)
        task.update_state(DownloadState.DOWNLOADING)
        task.mark_failed("err")
        with pytest.raises(InvalidStateTransitionError):
            task.retry()


# ---------------------------------------------------------------------------
# Serialization (to_dict / from_dict)
# ---------------------------------------------------------------------------


class TestSerialization:
    """Verify round-trip serialization to/from dict."""

    def test_round_trip(self):
        res = _make_resource(
            quality=VideoQuality.k1080p,
            languages=[LanguageType.kChs, LanguageType.kJp],
        )
        task = DownloadTask.from_resource_info(res, save_path="/downloads")
        task.update_state(DownloadState.DOWNLOADING)

        data = task.to_dict()
        restored = DownloadTask.from_dict(data)

        assert restored.id == task.id
        assert restored.state == DownloadState.DOWNLOADING
        assert restored.save_path == "/downloads"
        assert restored.resource_info.title == res.title
        assert restored.resource_info.quality == VideoQuality.k1080p
        assert LanguageType.kChs in restored.resource_info.languages

    def test_from_dict_with_string_state(self):
        data = _make_task().to_dict()
        data["state"] = "downloading"
        task = DownloadTask.from_dict(data)
        assert task.state == DownloadState.DOWNLOADING

    def test_from_dict_preserves_extra_data(self):
        task = _make_task()
        task.extra_data["task_id"] = "abc-123"
        data = task.to_dict()
        restored = DownloadTask.from_dict(data)
        assert restored.extra_data["task_id"] == "abc-123"

    def test_to_dict_returns_plain_dict(self):
        """Ensure to_dict result is JSON-serializable (no enum objects)."""
        task = _make_task()
        data = task.to_dict()
        # Should not raise
        serialized = json.dumps(data)
        assert isinstance(serialized, str)

    def test_from_dict_handles_empty_resource(self):
        """Ensure from_dict won't crash with minimal resource_info dict."""
        data = _make_task().to_dict()
        data["resource_info"] = {
            "title": "T",
            "download_url": "magnet:?x",
        }
        task = DownloadTask.from_dict(data)
        assert task.resource_info.title == "T"
