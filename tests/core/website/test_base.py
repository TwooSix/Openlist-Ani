"""Tests for WebsiteBase.fetch_feed robustness against network errors."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: F401

import aiohttp
import feedparser

from openlist_ani.core.website.common import CommonRSSWebsite
from openlist_ani.core.website.model import AnimeResourceInfo


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

    async def test_fetch_feed_success_normal_path(self):
        """Normal path: fetch RSS, parse entries, return results."""
        parser = CommonRSSWebsite()

        # Mock HTTP response
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text.return_value = "<rss>fake content</rss>"

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session

        # Mock feedparser
        mock_entry1 = MagicMock()
        mock_entry2 = MagicMock()
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry1, mock_entry2]

        # Mock parse_entry to return valid results
        anime1 = AnimeResourceInfo(
            title="[Sub] Anime 1 - 01",
            download_url="magnet:?xt=urn:btih:abc123"
        )
        anime2 = AnimeResourceInfo(
            title="[Sub] Anime 2 - 01",
            download_url="magnet:?xt=urn:btih:def456"
        )

        with (
            patch("aiohttp.ClientSession", return_value=mock_cm),
            patch("feedparser.parse", return_value=mock_feed),
            patch.object(parser, "parse_entry", new_callable=AsyncMock) as mock_parse
        ):
            mock_parse.side_effect = [anime1, anime2]
            result = await parser.fetch_feed("https://example.com/rss")

        assert len(result) == 2
        assert result[0].title == "[Sub] Anime 1 - 01"
        assert result[1].title == "[Sub] Anime 2 - 01"
        assert mock_parse.call_count == 2

    async def test_fetch_feed_parse_entry_exception_ignored(self):
        """parse_entry exceptions should be caught and ignored."""
        parser = CommonRSSWebsite()

        # Mock HTTP response
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text.return_value = "<rss>fake content</rss>"

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session

        # Mock feedparser with 3 entries
        mock_entries = [MagicMock(), MagicMock(), MagicMock()]
        mock_feed = MagicMock()
        mock_feed.entries = mock_entries

        # Mock parse_entry: first fails, second and third succeed
        anime2 = AnimeResourceInfo(
            title="[Sub] Anime 2 - 01",
            download_url="magnet:?xt=urn:btih:def456"
        )
        anime3 = AnimeResourceInfo(
            title="[Sub] Anime 3 - 01",
            download_url="magnet:?xt=urn:btih:ghi789"
        )

        with (
            patch("aiohttp.ClientSession", return_value=mock_cm),
            patch("feedparser.parse", return_value=mock_feed),
            patch.object(parser, "parse_entry", new_callable=AsyncMock) as mock_parse
        ):
            mock_parse.side_effect = [ValueError("Parse failed"), anime2, anime3]
            result = await parser.fetch_feed("https://example.com/rss")

        # Should get only the 2 successful results, exception ignored
        assert len(result) == 2
        assert result[0].title == "[Sub] Anime 2 - 01"
        assert result[1].title == "[Sub] Anime 3 - 01"

    async def test_fetch_feed_parse_entry_returns_none_filtered(self):
        """parse_entry returning None should be filtered out."""
        parser = CommonRSSWebsite()

        # Mock HTTP response
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text.return_value = "<rss>fake content</rss>"

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session

        # Mock feedparser with 2 entries
        mock_entries = [MagicMock(), MagicMock()]
        mock_feed = MagicMock()
        mock_feed.entries = mock_entries

        # Mock parse_entry: first returns None, second returns valid result
        anime2 = AnimeResourceInfo(
            title="[Sub] Valid Anime - 01",
            download_url="magnet:?xt=urn:btih:valid"
        )

        with (
            patch("aiohttp.ClientSession", return_value=mock_cm),
            patch("feedparser.parse", return_value=mock_feed),
            patch.object(parser, "parse_entry", new_callable=AsyncMock) as mock_parse
        ):
            mock_parse.side_effect = [None, anime2]
            result = await parser.fetch_feed("https://example.com/rss")

        # Should get only the 1 valid result, None filtered out
        assert len(result) == 1
        assert result[0].title == "[Sub] Valid Anime - 01"

    async def test_fetch_feed_empty_feed_returns_empty(self):
        """Empty RSS feed should return empty list."""
        parser = CommonRSSWebsite()

        # Mock HTTP response
        mock_response = AsyncMock()
        mock_response.raise_for_status.return_value = None
        mock_response.text.return_value = "<rss>empty feed</rss>"

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session

        # Mock feedparser with empty entries
        mock_feed = MagicMock()
        mock_feed.entries = []

        with (
            patch("aiohttp.ClientSession", return_value=mock_cm),
            patch("feedparser.parse", return_value=mock_feed)
        ):
            result = await parser.fetch_feed("https://example.com/rss")

        assert result == []
