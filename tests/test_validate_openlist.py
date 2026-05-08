"""Tests for OpenListHealthCheck runtime validation."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.adapters.outbound.configuration import ConfigManager
from openlist_ani.integrations.openlist import OpenListClient, OpenListHealthCheck


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """Create a ConfigManager with minimal valid config for openlist tests."""
    monkeypatch.chdir(tmp_path)
    m = ConfigManager("config.toml")
    m._config.rss.urls = ["http://feed"]
    m._config.openlist.url = "http://localhost:5244"
    m._config.openlist.token = "test-token"
    m._config.openlist.offline_download_tool = "qBittorrent"
    m.save()
    return m


async def _validate_with_client(mgr: ConfigManager, client: AsyncMock) -> bool:
    return await OpenListHealthCheck(
        client=client,
        base_url=mgr.openlist.url,
        offline_download_tool=mgr.openlist.offline_download_tool,
    ).validate()


class TestValidateOpenlist:
    @pytest.mark.asyncio
    async def test_health_check_fails(self, mgr):
        client = AsyncMock()
        client.is_healthy.return_value = False

        result = await _validate_with_client(mgr, client)

        assert result is False

    @pytest.mark.asyncio
    async def test_get_tools_returns_none(self, mgr):
        client = AsyncMock()
        client.is_healthy.return_value = True
        client.get_offline_download_tools.return_value = None

        result = await _validate_with_client(mgr, client)

        assert result is False

    @pytest.mark.asyncio
    async def test_tool_not_in_available_list(self, mgr):
        client = AsyncMock()
        client.is_healthy.return_value = True
        client.get_offline_download_tools.return_value = ["aria2"]

        result = await _validate_with_client(mgr, client)

        assert result is False

    @pytest.mark.asyncio
    async def test_all_checks_pass(self, mgr):
        client = AsyncMock()
        client.is_healthy.return_value = True
        client.get_offline_download_tools.return_value = ["qBittorrent", "aria2"]

        result = await _validate_with_client(mgr, client)

        assert result is True

    @pytest.mark.asyncio
    async def test_tool_name_matching_is_case_insensitive(self, mgr):
        mgr._config.openlist.offline_download_tool = "QBittorrent"
        client = AsyncMock()
        client.is_healthy.return_value = True
        client.get_offline_download_tools.return_value = ["qBittorrent", "aria2"]

        result = await _validate_with_client(mgr, client)

        assert result is True

    @pytest.mark.asyncio
    async def test_client_created_with_correct_params(self, mgr):
        with patch("aiohttp.ClientSession") as mock_session:
            mock_instance = AsyncMock()
            mock_session.return_value = mock_instance

            client = OpenListClient(base_url=mgr.openlist.url, token=mgr.openlist.token)
            await client.start()

        assert client.base_url == "http://localhost:5244"
        assert client.headers["Authorization"] == "test-token"

    @pytest.mark.asyncio
    async def test_dict_format_tools_also_work(self, mgr):
        client = AsyncMock()
        client.is_healthy.return_value = True
        client.get_offline_download_tools.return_value = [
            {"name": "qBittorrent"},
            {"name": "aria2"},
        ]

        result = await _validate_with_client(mgr, client)

        assert result is True

    @pytest.mark.asyncio
    async def test_empty_tools_list(self, mgr):
        client = AsyncMock()
        client.is_healthy.return_value = True
        client.get_offline_download_tools.return_value = []

        result = await _validate_with_client(mgr, client)

        assert result is False
