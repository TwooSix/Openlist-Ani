"""Tests for CommonRSSWebsite entry parsing."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openlist_ani.core.website.common import CommonRSSWebsite, _is_torrent_url


def _make_entry(
    title: str = "Test Anime - 01",
    download_url: str = "magnet:?xt=urn:btih:abc123",
    enclosures: list | None = None,
    link: str | None = None,
) -> SimpleNamespace:
    entry = SimpleNamespace(title=title, link=link or "")
    enc = enclosures or []
    entry.get = lambda key, default=None: enc if key == "enclosures" else default
    if not enclosures and download_url:
        enc.append({"href": download_url, "type": "application/x-bittorrent"})
    return entry


@pytest.fixture
def common_parser():
    return CommonRSSWebsite()


class TestCommonRSSWebsite:
    async def test_parse_entry_with_magnet(self, common_parser):
        entry = _make_entry(
            title="[SubGroup] Anime - 01 [1080p]",
            download_url="magnet:?xt=urn:btih:deadbeef",
        )
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None
        assert result.title == "[SubGroup] Anime - 01 [1080p]"
        assert result.download_url == "magnet:?xt=urn:btih:deadbeef"

    async def test_parse_entry_with_torrent_link(self, common_parser):
        entry = _make_entry(
            title="Anime - 02",
            enclosures=[{"href": "https://example.com/file.torrent", "type": ""}],
        )
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None
        assert result.download_url == "https://example.com/file.torrent"

    async def test_parse_entry_no_title_returns_none(self, common_parser):
        """Entry without title must be skipped, not crash."""
        entry = SimpleNamespace(title=None, link="")
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is None

    async def test_parse_entry_no_download_url_returns_none(self, common_parser):
        """Entry without any download link must be skipped."""
        entry = SimpleNamespace(title="Some Title", link="https://example.com/page")
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is None

    async def test_parse_entry_fallback_to_link(self, common_parser):
        """When no valid enclosure, fall back to link attribute if it's a magnet."""
        entry = SimpleNamespace(
            title="Anime - 03",
            link="magnet:?xt=urn:btih:fallback",
        )
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None
        assert result.download_url == "magnet:?xt=urn:btih:fallback"

    async def test_parse_entry_empty_enclosures(self, common_parser):
        """Empty enclosures list and non-torrent link → None."""
        entry = SimpleNamespace(title="Anime", link="https://example.com")
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is None

    async def test_parse_entry_missing_enclosures_key(self, common_parser):
        """Entry missing enclosures attribute entirely."""
        entry = SimpleNamespace(title="Anime", link="magnet:?xt=urn:btih:ok")
        entry.get = lambda key, default=None: default
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None

    async def test_get_download_url_type_bittorrent_non_magnet_non_torrent(
        self, common_parser
    ):
        """Enclosure with type='application/x-bittorrent' but href is
        neither a magnet link nor a .torrent URL should still be accepted
        because the type field alone triggers the match."""
        entry = _make_entry(
            title="Anime - 04",
            download_url="",
            enclosures=[
                {
                    "href": "https://tracker.example.com/download/12345",
                    "type": "application/x-bittorrent",
                }
            ],
        )
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None
        assert result.download_url == "https://tracker.example.com/download/12345"

    async def test_torrent_url_with_query_params_enclosure(self, common_parser):
        """Torrent URL with query parameters (e.g. passkey) must be accepted.

        Regression test: endswith('.torrent') fails for URLs like
        'https://example.com/file.torrent?passkey=abc123'.
        """
        torrent_url = "https://tracker.example.com/file.torrent?passkey=abc123"
        entry = _make_entry(
            title="Anime - 05",
            download_url="",
            enclosures=[{"href": torrent_url, "type": ""}],
        )
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None
        assert result.download_url == torrent_url

    async def test_torrent_url_with_query_params_link_fallback(self, common_parser):
        """Link fallback with .torrent?query must also be accepted."""
        torrent_url = "https://tracker.example.com/dl.torrent?id=99&passkey=x"
        entry = SimpleNamespace(title="Anime - 06", link=torrent_url)
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await common_parser.parse_entry(entry, session)
        assert result is not None
        assert result.download_url == torrent_url


class TestIsTorrentUrl:
    """Unit tests for the _is_torrent_url helper."""

    def test_plain_torrent_url(self):
        assert _is_torrent_url("https://example.com/file.torrent") is True

    def test_torrent_url_with_query_params(self):
        assert (
            _is_torrent_url("https://example.com/file.torrent?passkey=abc123") is True
        )

    def test_torrent_url_with_fragment(self):
        assert _is_torrent_url("https://example.com/file.torrent#section") is True

    def test_non_torrent_url(self):
        assert _is_torrent_url("https://example.com/page.html") is False

    def test_empty_string(self):
        assert _is_torrent_url("") is False

    def test_magnet_link(self):
        assert _is_torrent_url("magnet:?xt=urn:btih:abc") is False
