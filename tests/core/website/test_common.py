"""Tests for CommonRSSWebsite entry parsing."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openlist_ani.core.website.common import CommonRSSWebsite


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
