"""Tests for download API models (OpenlistTask, FileEntry, OfflineDownloadTool, etc.)."""

import json

from openlist_ani.core.download.api.model import (
    FileEntry,
    OfflineDownloadTool,
    OpenlistTask,
    OpenlistTaskState,
    _parse_iso,
)

# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    """Verify ISO-8601 datetime parsing edge cases."""

    def test_none_returns_none(self):
        assert _parse_iso(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso("") is None

    def test_basic_iso(self):
        dt = _parse_iso("2025-06-15T12:30:00")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.hour == 12

    def test_trailing_z(self):
        dt = _parse_iso("2025-01-01T00:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_fractional_seconds_too_long(self):
        """Fractional seconds > 6 digits should be truncated, not crash."""
        dt = _parse_iso("2025-01-01T00:00:00.123456789")
        assert dt is not None
        assert dt.microsecond == 123456

    def test_fractional_with_timezone(self):
        dt = _parse_iso("2025-01-01T00:00:00.123456789+08:00")
        assert dt is not None

    def test_garbage_returns_none(self):
        assert _parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# OpenlistTaskState enum
# ---------------------------------------------------------------------------


class TestOpenlistTaskState:
    def test_succeeded_value(self):
        assert OpenlistTaskState.SUCCEEDED.value == 2

    def test_all_states_unique(self):
        values = [s.value for s in OpenlistTaskState]
        assert len(values) == len(set(values))


# ---------------------------------------------------------------------------
# OfflineDownloadTool enum
# ---------------------------------------------------------------------------


class TestOfflineDownloadTool:
    def test_qbittorrent_string(self):
        assert str(OfflineDownloadTool.QBITTORRENT) == "qBittorrent"

    def test_from_string(self):
        tool = OfflineDownloadTool("aria2")
        assert tool == OfflineDownloadTool.ARIA2


# ---------------------------------------------------------------------------
# OpenlistTask.from_dict
# ---------------------------------------------------------------------------


class TestOpenlistTaskFromDict:
    def test_minimal(self):
        t = OpenlistTask.from_dict({"id": "1", "name": "test"})
        assert t.id == "1"
        assert t.name == "test"
        assert t.state is None

    def test_with_state(self):
        t = OpenlistTask.from_dict({"id": "1", "name": "t", "state": 2})
        assert t.state == OpenlistTaskState.SUCCEEDED

    def test_invalid_state_value(self):
        """Unknown state integer should not crash, state becomes None."""
        t = OpenlistTask.from_dict({"id": "1", "name": "t", "state": 9999})
        assert t.state is None

    def test_with_timestamps(self):
        t = OpenlistTask.from_dict(
            {
                "id": "1",
                "name": "t",
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-01T01:00:00Z",
            }
        )
        assert t.start_time is not None
        assert t.end_time is not None

    def test_missing_fields_default_to_none(self):
        t = OpenlistTask.from_dict({"id": "1", "name": "t"})
        assert t.progress is None
        assert t.total_bytes is None
        assert t.error is None
        assert t.creator is None

    def test_empty_dict(self):
        """Empty dict should not crash — id/name default to empty string."""
        t = OpenlistTask.from_dict({})
        assert t.id == ""
        assert t.name == ""


# ---------------------------------------------------------------------------
# FileEntry.from_dict
# ---------------------------------------------------------------------------


class TestFileEntryFromDict:
    def test_minimal(self):
        f = FileEntry.from_dict({"name": "video.mp4"})
        assert f.name == "video.mp4"
        assert f.is_directory is False

    def test_is_dir_flag(self):
        f = FileEntry.from_dict({"name": "folder", "is_dir": True})
        assert f.is_directory is True

    def test_size_from_bytes_key(self):
        f = FileEntry.from_dict({"name": "f", "bytes": 1024})
        assert f.size == 1024

    def test_size_from_total_bytes_key(self):
        f = FileEntry.from_dict({"name": "f", "total_bytes": 2048})
        assert f.size == 2048

    def test_path_from_full_path(self):
        f = FileEntry.from_dict({"name": "f", "full_path": "/a/b/c"})
        assert f.path == "/a/b/c"

    def test_hash_info_from_json_string(self):
        """hashinfo as JSON string should be parsed."""
        f = FileEntry.from_dict(
            {
                "name": "f",
                "hashinfo": json.dumps({"md5": "abc"}),
            }
        )
        assert f.hash_info == {"md5": "abc"}

    def test_hash_info_invalid_json_no_crash(self):
        """Invalid JSON in hashinfo should not crash."""
        f = FileEntry.from_dict({"name": "f", "hashinfo": "not-json"})
        assert f.hash_info is None

    def test_empty_dict(self):
        """Empty dict should not crash."""
        f = FileEntry.from_dict({})
        assert f.name == ""
