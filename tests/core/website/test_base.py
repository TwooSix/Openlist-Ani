"""Tests for WebsiteBase.fetch_feed robustness against network errors."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from openlist_ani.core.website.common import CommonRSSWebsite


class TestWebsiteBaseFetchFeed:
    """Test that fetch_feed handles network errors gracefully."""

    async def test_fetch_feed_timeout_returns_empty(self):
        """Timeout during HTTP request must return [], not raise."""
        parser = CommonRSSWebsite()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.side_effect = asyncio.TimeoutError()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session

        with patch("aiohttp.ClientSession", return_value=mock_cm):
            result = await parser.fetch_feed("https://example.com/rss")

        assert result == []

    async def test_fetch_feed_http_error_returns_empty(self):
        """HTTP errors must return [], not crash."""
        parser = CommonRSSWebsite()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.side_effect = aiohttp.ClientError("Connection refused")

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session

        with patch("aiohttp.ClientSession", return_value=mock_cm):
            result = await parser.fetch_feed("https://example.com/rss")

        assert result == []

    async def test_fetch_feed_logs_parse_entry_exceptions(self):
        """parse_entry exceptions must be logged, not silently swallowed.

        Regression test: previously, exceptions from parse_entry were
        caught by asyncio.gather(return_exceptions=True) but never logged,
        making it impossible to diagnose why feeds returned empty results.
        """
        parser = CommonRSSWebsite()

        # Build minimal RSS XML with one entry
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>Test Entry</title>
              <link>magnet:?xt=urn:btih:abc</link>
            </item>
          </channel>
        </rss>"""

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=rss_xml)

        mock_resp_ctx = AsyncMock()
        mock_resp_ctx.__aenter__.return_value = mock_response
        mock_resp_ctx.__aexit__.return_value = False

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp_ctx

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = False

        # Make parse_entry raise an exception
        error_msg = "Unexpected HTML structure"
        with (
            patch("aiohttp.ClientSession", return_value=mock_session_ctx),
            patch.object(
                parser,
                "parse_entry",
                new_callable=AsyncMock,
                side_effect=ValueError(error_msg),
            ),
            patch("openlist_ani.core.website.base.logger") as mock_logger,
        ):
            result = await parser.fetch_feed("https://example.com/rss")

        # Result should be empty (the failing entry is skipped)
        assert result == []

        # But the exception MUST have been logged
        mock_logger.warning.assert_called()
        logged_message = mock_logger.warning.call_args[0][0]
        assert error_msg in logged_message
