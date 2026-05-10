from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

import openlist_ani.logger as logger_module
from openlist_ani.adapters.outbound.feed_sources import ReleaseFeedReader
from openlist_ani.domain.anime_release import AnimeRelease


@pytest.fixture
def captured_logs():
    sink = StringIO()
    logger_module.logger.remove()
    logger_module.logger.add(sink, level="DEBUG", format="{level}|{message}")
    try:
        yield sink
    finally:
        logger_module.configure_logger()


async def test_empty_urls_returns_empty():
    reader = ReleaseFeedReader([])
    result = await reader.fetch_new_releases()

    assert result == []


async def test_entries_from_multiple_feeds_are_merged():
    reader = ReleaseFeedReader(["https://a.com/rss", "https://b.com/rss"])
    r1 = AnimeRelease(title="Anime A - 01", download_url="magnet:?a")
    r2 = AnimeRelease(title="Anime B - 01", download_url="magnet:?b")

    mock_handler = AsyncMock()
    mock_handler.fetch_feed = AsyncMock(side_effect=[[r1], [r2]])

    with (patch.object(reader, "_get_feed_source", return_value=mock_handler),):
        result = await reader.fetch_new_releases()

    assert {entry.title for entry in result} == {"Anime A - 01", "Anime B - 01"}


async def test_entries_without_download_url_are_skipped():
    reader = ReleaseFeedReader(["https://acg.rip/.xml"])
    release_no_url = AnimeRelease(title="No URL Anime", download_url="")
    release_good = AnimeRelease(
        title="Good Anime - 01",
        download_url="magnet:?xt=urn:btih:good",
    )

    mock_handler = AsyncMock()
    mock_handler.fetch_feed = AsyncMock(return_value=[release_no_url, release_good])

    with (patch.object(reader, "_get_feed_source", return_value=mock_handler),):
        result = await reader.fetch_new_releases()

    assert [entry.title for entry in result] == ["Good Anime - 01"]


async def test_fetch_exception_does_not_crash():
    reader = ReleaseFeedReader(["https://fail.example.com/rss"])
    mock_handler = AsyncMock()
    mock_handler.fetch_feed = AsyncMock(side_effect=Exception("Network error"))

    with (patch.object(reader, "_get_feed_source", return_value=mock_handler),):
        result = await reader.fetch_new_releases()

    assert result == []


async def test_fetch_exception_logs_redacted_source_and_continues(captured_logs):
    reader = ReleaseFeedReader(
        [
            "https://fail.example.com/rss?token=feed-secret",
            "https://ok.example.com/rss",
        ]
    )
    release_good = AnimeRelease(
        title="Good Anime - 01",
        download_url="magnet:?xt=urn:btih:good",
    )
    fail_handler = AsyncMock()
    fail_handler.fetch_feed = AsyncMock(side_effect=Exception("Network error"))
    ok_handler = AsyncMock()
    ok_handler.fetch_feed = AsyncMock(return_value=[release_good])

    with patch.object(
        reader, "_get_feed_source", side_effect=[fail_handler, ok_handler]
    ):
        result = await reader.fetch_new_releases()

    output = captured_logs.getvalue()
    assert [entry.title for entry in result] == ["Good Anime - 01"]
    assert "fail.example.com/rss" in output
    assert "token=<redacted>" in output
    assert "feed-secret" not in output
