"""Tests for OpenListClient API methods, retry logic, and auth guards."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openlist_ani.core.download.api.client import OpenListClient
from openlist_ani.core.download.api.model import (
    FileEntry,
    OfflineDownloadTool,
    OpenlistTask,
    OpenlistTaskState,
)

BASE_URL = "http://localhost:5244"


@pytest.fixture
def client():
    """Create a basic OpenListClient with a valid token."""
    return OpenListClient(
        base_url=BASE_URL,
        token="test-token",
        max_retries=1,
    )


@pytest.fixture
def no_token_client():
    """Create an OpenListClient without a token."""
    return OpenListClient(
        base_url=BASE_URL,
        token="",
        max_retries=1,
    )


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


class TestCheckHealth:
    async def test_health_success(self, client):
        """Should return True when server responds with code 200."""
        with patch.object(
            client,
            "_get",
            new_callable=AsyncMock,
            return_value={"code": 200, "data": {}},
        ):
            result = await client.is_healthy()
            assert result is True

    async def test_health_failure_non_200(self, client):
        """Should return False when server responds with non-200 code."""
        with patch.object(
            client,
            "_get",
            new_callable=AsyncMock,
            return_value={"code": 500, "message": "Server error"},
        ):
            result = await client.is_healthy()
            assert result is False

    async def test_health_failure_none(self, client):
        """Should return False when request returns None (network error)."""
        with patch.object(
            client,
            "_get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await client.is_healthy()
            assert result is False

    async def test_health_calls_correct_url(self, client):
        """Should call the public settings endpoint."""
        mock_get = AsyncMock(return_value={"code": 200, "data": {}})
        with patch.object(client, "_get", mock_get):
            await client.is_healthy()
            mock_get.assert_called_once_with(f"{BASE_URL}/api/public/settings")


# ---------------------------------------------------------------------------
# add_offline_download
# ---------------------------------------------------------------------------


class TestAddOfflineDownload:
    async def test_success_returns_task_objects(self, client):
        """Should parse response into OpenlistTask objects on code 200."""
        mock_response = {
            "code": 200,
            "data": {
                "tasks": [
                    {
                        "id": "task-abc",
                        "name": "download ep01",
                        "state": OpenlistTaskState.RUNNING.value,
                    }
                ]
            },
        }
        mock_post = AsyncMock(return_value=mock_response)
        with patch.object(client, "_post", mock_post):
            result = await client.add_offline_download(
                urls=["magnet:?xt=urn:btih:hash1"],
                path="/anime/staging",
                tool=OfflineDownloadTool.ARIA2,
                delete_policy="delete_always",
            )

        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], OpenlistTask)
        assert result[0].id == "task-abc"

        mock_post.assert_called_once_with(
            f"{BASE_URL}/api/fs/add_offline_download",
            {
                "urls": ["magnet:?xt=urn:btih:hash1"],
                "path": "/anime/staging",
                "tool": "aria2",
                "delete_policy": "delete_always",
            },
        )

    async def test_failure_returns_none(self, client):
        """Should return None when API responds with a non-200 code."""
        mock_post = AsyncMock(return_value={"code": 500, "message": "internal error"})
        with patch.object(client, "_post", mock_post):
            result = await client.add_offline_download(
                urls=["magnet:?xt=hash"],
                path="/downloads/anime",
                tool="aria2",
            )
        assert result is None

    async def test_network_error_returns_none(self, client):
        """Should return None when _post returns None (network failure)."""
        mock_post = AsyncMock(return_value=None)
        with patch.object(client, "_post", mock_post):
            result = await client.add_offline_download(
                urls=["magnet:?xt=hash"],
                path="/downloads/anime",
                tool="aria2",
            )
        assert result is None

    async def test_success_with_empty_tasks(self, client):
        """Should return empty list when no tasks in response."""
        mock_post = AsyncMock(return_value={"code": 200, "data": {"tasks": []}})
        with patch.object(client, "_post", mock_post):
            result = await client.add_offline_download(
                urls=["magnet:?xt=hash"],
                path="/downloads/anime",
                tool="aria2",
            )
        assert result == []

    async def test_success_with_null_tasks_field(self, client):
        """Should handle None tasks field gracefully."""
        mock_post = AsyncMock(return_value={"code": 200, "data": {"tasks": None}})
        with patch.object(client, "_post", mock_post):
            result = await client.add_offline_download(
                urls=["magnet:?xt=hash"],
                path="/downloads/anime",
                tool="aria2",
            )
        assert result == []

    async def test_tool_accepts_string(self, client):
        """Should accept a plain string as the tool parameter."""
        mock_post = AsyncMock(return_value={"code": 200, "data": {"tasks": []}})
        with patch.object(client, "_post", mock_post):
            await client.add_offline_download(
                urls=["url1"],
                path="/p",
                tool="qBittorrent",
            )

        payload = mock_post.call_args[0][1]
        assert payload["tool"] == "qBittorrent"


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class TestListFiles:
    async def test_success_returns_file_entries(self, client):
        """Should parse response content into FileEntry objects."""
        mock_response = {
            "code": 200,
            "data": {
                "content": [
                    {"name": "ep01.mkv", "size": 1024, "is_dir": False},
                    {"name": "subs", "size": 0, "is_dir": True},
                ]
            },
        }
        mock_post = AsyncMock(return_value=mock_response)
        with patch.object(client, "_post", mock_post):
            result = await client.list_files("/anime/folder")

        assert result is not None
        assert len(result) == 2
        assert all(isinstance(f, FileEntry) for f in result)
        assert result[0].name == "ep01.mkv"
        assert result[0].size == 1024
        assert result[0].is_dir is False
        assert result[1].name == "subs"
        assert result[1].is_directory is True

        mock_post.assert_called_once_with(
            f"{BASE_URL}/api/fs/list",
            {
                "path": "/anime/folder",
                "password": "",
                "page": 1,
                "per_page": 0,
                "refresh": True,
            },
        )

    async def test_success_empty_content(self, client):
        """Should return empty list when content is null/empty."""
        mock_post = AsyncMock(return_value={"code": 200, "data": {"content": None}})
        with patch.object(client, "_post", mock_post):
            result = await client.list_files("/empty")
        assert result == []

    async def test_success_null_data_field(self, client):
        """Should return empty list when data field is null.

        Regression: the server may return {"code": 200, "data": null}
        for an empty directory.  Before the fix this caused an
        AttributeError because ``None.get("content")`` was called.
        """
        mock_post = AsyncMock(return_value={"code": 200, "data": None})
        with patch.object(client, "_post", mock_post):
            result = await client.list_files("/empty-dir")
        assert result == []

    async def test_failure_returns_none(self, client):
        """Should return None when API responds with non-200."""
        mock_post = AsyncMock(return_value={"code": 403, "message": "forbidden"})
        with patch.object(client, "_post", mock_post):
            result = await client.list_files("/forbidden")
        assert result is None

    async def test_network_error_returns_none(self, client):
        """Should return None when _post returns None."""
        mock_post = AsyncMock(return_value=None)
        with patch.object(client, "_post", mock_post):
            result = await client.list_files("/path")
        assert result is None


# ---------------------------------------------------------------------------
# rename_file
# ---------------------------------------------------------------------------


class TestRenameFile:
    async def test_success(self, client):
        """Should return True and send correct payload on success."""
        mock_post = AsyncMock(return_value={"code": 200})
        with patch.object(client, "_post", mock_post):
            result = await client.rename_file("/anime/ep01.mkv", "episode_01.mkv")

        assert result is True
        mock_post.assert_called_once_with(
            f"{BASE_URL}/api/fs/rename",
            {"path": "/anime/ep01.mkv", "name": "episode_01.mkv"},
        )

    async def test_failure_returns_false(self, client):
        """Should return False on non-200 response."""
        mock_post = AsyncMock(return_value={"code": 500, "message": "rename failed"})
        with patch.object(client, "_post", mock_post):
            result = await client.rename_file("/anime/ep01.mkv", "new.mkv")
        assert result is False

    async def test_network_error_returns_false(self, client):
        """Should return False when _post returns None."""
        mock_post = AsyncMock(return_value=None)
        with patch.object(client, "_post", mock_post):
            result = await client.rename_file("/anime/file.mkv", "new.mkv")
        assert result is False


# ---------------------------------------------------------------------------
# mkdir
# ---------------------------------------------------------------------------


class TestMkdir:
    async def test_success(self, client):
        """Should return True and send correct payload on success."""
        mock_post = AsyncMock(return_value={"code": 200})
        with patch.object(client, "_post", mock_post):
            result = await client.mkdir("/anime/Season 1")

        assert result is True
        mock_post.assert_called_once_with(
            f"{BASE_URL}/api/fs/mkdir",
            {"path": "/anime/Season 1"},
        )

    async def test_failure_returns_false(self, client):
        """Should return False on non-200 response."""
        mock_post = AsyncMock(return_value={"code": 500, "message": "mkdir failed"})
        with patch.object(client, "_post", mock_post):
            result = await client.mkdir("/anime/Season 1")
        assert result is False

    async def test_network_error_returns_false(self, client):
        """Should return False when _post returns None."""
        mock_post = AsyncMock(return_value=None)
        with patch.object(client, "_post", mock_post):
            result = await client.mkdir("/anime/Season 1")
        assert result is False


# ---------------------------------------------------------------------------
# move_file
# ---------------------------------------------------------------------------


class TestMoveFile:
    async def test_success(self, client):
        """Should return True and send correct payload on success."""
        mock_post = AsyncMock(return_value={"code": 200})
        with patch.object(client, "_post", mock_post):
            result = await client.move_file(
                "/downloads/staging", "/anime/Season 1", ["ep01.mkv"]
            )

        assert result is True
        mock_post.assert_called_once_with(
            f"{BASE_URL}/api/fs/move",
            {
                "src_dir": "/downloads/staging",
                "dst_dir": "/anime/Season 1",
                "names": ["ep01.mkv"],
            },
        )

    async def test_failure_returns_false(self, client):
        """Should return False on non-200 response."""
        mock_post = AsyncMock(return_value={"code": 500, "message": "move failed"})
        with patch.object(client, "_post", mock_post):
            result = await client.move_file("/src", "/dst", ["file.mkv"])
        assert result is False

    async def test_network_error_returns_false(self, client):
        """Should return False when _post returns None."""
        mock_post = AsyncMock(return_value=None)
        with patch.object(client, "_post", mock_post):
            result = await client.move_file("/src", "/dst", ["file.mkv"])
        assert result is False

    async def test_multiple_filenames(self, client):
        """Should pass multiple filenames in the names list."""
        mock_post = AsyncMock(return_value={"code": 200})
        with patch.object(client, "_post", mock_post):
            result = await client.move_file("/src", "/dst", ["file1.mkv", "file2.mp4"])

        assert result is True
        payload = mock_post.call_args[0][1]
        assert payload["names"] == ["file1.mkv", "file2.mp4"]


# ---------------------------------------------------------------------------
# remove_path
# ---------------------------------------------------------------------------


class TestRemovePath:
    async def test_success(self, client):
        """Should return True and send correct payload on success."""
        mock_post = AsyncMock(return_value={"code": 200})
        with patch.object(client, "_post", mock_post):
            result = await client.remove_path("/anime", ["tmp_dir"])

        assert result is True
        mock_post.assert_called_once_with(
            f"{BASE_URL}/api/fs/remove",
            {"dir": "/anime", "names": ["tmp_dir"]},
        )

    async def test_failure_returns_false(self, client):
        """Should return False on non-200 response."""
        mock_post = AsyncMock(return_value={"code": 500, "message": "remove failed"})
        with patch.object(client, "_post", mock_post):
            result = await client.remove_path("/anime", ["dir"])
        assert result is False

    async def test_network_error_returns_false(self, client):
        """Should return False when _post returns None."""
        mock_post = AsyncMock(return_value=None)
        with patch.object(client, "_post", mock_post):
            result = await client.remove_path("/anime", ["dir"])
        assert result is False

    async def test_multiple_names(self, client):
        """Should pass multiple names to remove."""
        mock_post = AsyncMock(return_value={"code": 200})
        with patch.object(client, "_post", mock_post):
            result = await client.remove_path("/root", ["a", "b", "c"])

        assert result is True
        payload = mock_post.call_args[0][1]
        assert payload["names"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _request retry/backoff
# ---------------------------------------------------------------------------


class TestRequestRetry:
    async def test_retry_on_client_error_then_succeed(self):
        """Should retry on aiohttp.ClientError and succeed on second attempt."""
        client = OpenListClient(
            base_url=BASE_URL,
            token="tok",
            max_retries=3,
            retry_backoff_seconds=0.01,
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"code": 200})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(
            side_effect=[
                aiohttp.ClientError("connection reset"),
                mock_response,
            ]
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await client._request("GET", f"{BASE_URL}/test")

        assert result == {"code": 200}
        mock_sleep.assert_awaited_once()

    async def test_retry_exhausted_returns_none(self):
        """Should return None after all retries are exhausted."""
        client = OpenListClient(
            base_url=BASE_URL,
            token="tok",
            max_retries=2,
            retry_backoff_seconds=0.01,
        )

        mock_session = AsyncMock()
        mock_session.request = MagicMock(
            side_effect=aiohttp.ClientError("always fails")
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client._request("POST", f"{BASE_URL}/fail")

        assert result is None

    async def test_non_network_error_not_retried(self):
        """Non-network errors (e.g. ValueError) should not be retried."""
        client = OpenListClient(
            base_url=BASE_URL,
            token="tok",
            max_retries=3,
            retry_backoff_seconds=0.01,
        )

        mock_session = AsyncMock()
        mock_session.request = MagicMock(side_effect=ValueError("JSON decode error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await client._request("GET", f"{BASE_URL}/bad")

        assert result is None
        mock_sleep.assert_not_awaited()

    async def test_timeout_error_triggers_retry(self):
        """asyncio.TimeoutError should trigger retry."""
        client = OpenListClient(
            base_url=BASE_URL,
            token="tok",
            max_retries=2,
            retry_backoff_seconds=0.01,
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={"code": 200})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(
            side_effect=[
                asyncio.TimeoutError(),
                mock_response,
            ]
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client._request("GET", f"{BASE_URL}/slow")

        assert result == {"code": 200}

    async def test_backoff_increases_exponentially(self):
        """Backoff duration should double with each retry attempt."""
        client = OpenListClient(
            base_url=BASE_URL,
            token="tok",
            max_retries=3,
            retry_backoff_seconds=1.0,
        )

        mock_session = AsyncMock()
        mock_session.request = MagicMock(side_effect=aiohttp.ClientError("fail"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await client._request("GET", f"{BASE_URL}/test")

        # 3 retries → 2 sleeps: backoff * 2^0 = 1.0, backoff * 2^1 = 2.0
        assert mock_sleep.await_count == 2
        assert mock_sleep.await_args_list[0].args[0] == pytest.approx(1.0)
        assert mock_sleep.await_args_list[1].args[0] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Auth guard — methods should short-circuit without a token
# ---------------------------------------------------------------------------


class TestAuthGuard:
    """Methods requiring auth should return None/False when token is empty."""

    async def test_add_offline_download_no_token(self, no_token_client):
        result = await no_token_client.add_offline_download(
            urls=["url"], path="/p", tool="aria2"
        )
        assert result is None

    async def test_list_files_no_token(self, no_token_client):
        result = await no_token_client.list_files("/path")
        assert result is None

    async def test_rename_file_no_token(self, no_token_client):
        result = await no_token_client.rename_file("/path/file", "new")
        assert result is False

    async def test_mkdir_no_token(self, no_token_client):
        result = await no_token_client.mkdir("/path")
        assert result is False

    async def test_move_file_no_token(self, no_token_client):
        result = await no_token_client.move_file("/src", "/dst", ["file"])
        assert result is False

    async def test_remove_path_no_token(self, no_token_client):
        result = await no_token_client.remove_path("/dir", ["name"])
        assert result is False

    async def test_no_post_called_when_no_token(self, no_token_client):
        """Ensure _post is never called when token is missing."""
        mock_post = AsyncMock()
        with patch.object(no_token_client, "_post", mock_post):
            await no_token_client.add_offline_download(["u"], "/p", "aria2")
            await no_token_client.list_files("/p")
            await no_token_client.rename_file("/f", "n")
            await no_token_client.mkdir("/d")
            await no_token_client.move_file("/s", "/d", ["f"])
            await no_token_client.remove_path("/d", ["n"])

        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Offline download transfer task APIs (existing coverage extended)
# ---------------------------------------------------------------------------


class TestOfflineDownloadTransferTaskApis:
    async def test_get_transfer_done_success(self, client):
        mock_get = AsyncMock(
            return_value={
                "code": 200,
                "data": [
                    {
                        "id": "transfer-1",
                        "name": "transfer for uuid 123",
                        "state": OpenlistTaskState.SUCCEEDED.value,
                    }
                ],
            }
        )
        with patch.object(client, "_get", mock_get):
            result = await client.get_offline_download_transfer_done()

        assert result is not None
        assert len(result) == 1
        assert result[0].id == "transfer-1"
        assert result[0].state == OpenlistTaskState.SUCCEEDED
        mock_get.assert_called_once_with(
            f"{BASE_URL}/api/task/offline_download_transfer/done"
        )

    async def test_get_transfer_undone_success(self, client):
        mock_get = AsyncMock(
            return_value={
                "code": 200,
                "data": [
                    {
                        "id": "transfer-2",
                        "name": "transfer for uuid 456",
                        "state": OpenlistTaskState.RUNNING.value,
                    }
                ],
            }
        )
        with patch.object(client, "_get", mock_get):
            result = await client.get_offline_download_transfer_undone()

        assert result is not None
        assert len(result) == 1
        assert result[0].id == "transfer-2"
        assert result[0].state == OpenlistTaskState.RUNNING
        mock_get.assert_called_once_with(
            f"{BASE_URL}/api/task/offline_download_transfer/undone"
        )

    async def test_get_transfer_done_returns_none_on_failure(self, client):
        with patch.object(
            client,
            "_get",
            new_callable=AsyncMock,
            return_value={"code": 500, "message": "error"},
        ):
            result = await client.get_offline_download_transfer_done()

        assert result is None
