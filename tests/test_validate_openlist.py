"""Tests for ConfigManager.validate_openlist() runtime validation."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.config import ConfigManager
from openlist_ani.core.download.api.model import OfflineDownloadTool


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """Create a ConfigManager with minimal valid config for openlist tests."""
    monkeypatch.chdir(tmp_path)
    m = ConfigManager("config.toml")
    m._config.rss.urls = ["http://feed"]
    m._config.openlist.url = "http://localhost:5244"
    m._config.openlist.token = "test-token"
    m._config.openlist.offline_download_tool = OfflineDownloadTool.QBITTORRENT
    m.save()
    return m


class TestValidateOpenlist:
    @pytest.mark.asyncio
    async def test_health_check_fails(self, mgr):
        """Should return False when health check fails."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = False
            MockClient.return_value = mock_instance

            result = await mgr.validate_openlist()
            assert result is False
            mock_instance.is_healthy.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tools_returns_none(self, mgr):
        """Should return False when get_offline_download_tools returns None."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = True
            mock_instance.get_offline_download_tools.return_value = None
            MockClient.return_value = mock_instance

            result = await mgr.validate_openlist()
            assert result is False

    @pytest.mark.asyncio
    async def test_tool_not_in_available_list(self, mgr):
        """Should return False when configured tool is not available on server."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = True
            mock_instance.get_offline_download_tools.return_value = [
                "aria2",
            ]
            MockClient.return_value = mock_instance

            result = await mgr.validate_openlist()
            assert result is False

    @pytest.mark.asyncio
    async def test_all_checks_pass(self, mgr):
        """Should return True when health is ok and tool is available."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = True
            mock_instance.get_offline_download_tools.return_value = [
                "qBittorrent",
                "aria2",
            ]
            MockClient.return_value = mock_instance

            result = await mgr.validate_openlist()
            assert result is True

    @pytest.mark.asyncio
    async def test_client_created_with_correct_params(self, mgr):
        """Should create OpenListClient with url and token from config."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = True
            mock_instance.get_offline_download_tools.return_value = [
                "qBittorrent",
            ]
            MockClient.return_value = mock_instance

            await mgr.validate_openlist()
            MockClient.assert_called_once_with(
                base_url="http://localhost:5244",
                token="test-token",
            )

    @pytest.mark.asyncio
    async def test_dict_format_tools_also_work(self, mgr):
        """Should also handle dict-format tools (e.g. {"name": "qBittorrent"})."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = True
            mock_instance.get_offline_download_tools.return_value = [
                {"name": "qBittorrent"},
                {"name": "aria2"},
            ]
            MockClient.return_value = mock_instance

            result = await mgr.validate_openlist()
            assert result is True

    @pytest.mark.asyncio
    async def test_empty_tools_list(self, mgr):
        """Should return False when server returns empty tools list."""
        with patch("openlist_ani.core.download.api.OpenListClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_healthy.return_value = True
            mock_instance.get_offline_download_tools.return_value = []
            MockClient.return_value = mock_instance

            result = await mgr.validate_openlist()
            assert result is False
