"""Tests for worker module (batch dispatch)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openlist_ani.core.website.model import AnimeResourceInfo, VideoQuality


class _StopPoll(Exception):
    """Sentinel exception used to break the worker's poll loop in tests."""


def _make_resource(
    title: str = "Test Anime - 01",
    download_url: str = "magnet:?xt=urn:btih:abc123",
) -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url=download_url)


def _parse_ok(
    anime_name="Anime",
    season=1,
    episode=1,
    quality=VideoQuality.Q1080P,
    fansub=None,
    languages=None,
    version=1,
):
    return SimpleNamespace(
        success=True,
        result=SimpleNamespace(
            anime_name=anime_name,
            season=season,
            episode=episode,
            quality=quality,
            fansub=fansub,
            languages=languages or [],
            version=version,
        ),
        error=None,
    )


def _parse_fail(error="failed"):
    return SimpleNamespace(success=False, result=None, error=error)


async def _run_dispatch_once(queue, mock_manager):
    """Run DownloadDispatcher until it processes one batch, then cancel."""
    from openlist_ani.backend.worker import DownloadDispatcher

    active: set[asyncio.Task] = set()

    with (patch("openlist_ani.backend.worker.config") as mock_config,):
        mock_config.openlist.download_path = "/downloads"

        try:
            async with asyncio.timeout(0.02):
                await DownloadDispatcher(mock_manager, queue, active).run()
        except TimeoutError:
            pass


async def _run_poll_once(rss_entries, parse_results):
    """Run RSSPollWorker for one cycle and return queued entries."""
    from openlist_ani.backend.worker import RSSPollWorker

    queue: asyncio.Queue = asyncio.Queue()
    mock_rss = AsyncMock()
    mock_rss.check_update = AsyncMock(return_value=rss_entries)

    async def _cancel_sleep(_seconds: float) -> None:
        """Interrupt the worker's inter-poll sleep so we exit immediately."""
        raise _StopPoll

    with (
        patch(
            "openlist_ani.backend.worker.parse_metadata",
            new_callable=AsyncMock,
            return_value=parse_results,
        ) as mock_parse,
        patch("openlist_ani.backend.worker.config") as mock_config,
        patch(
            "openlist_ani.core.rss.filter.priority.db.find_resources_by_episode",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "openlist_ani.backend.worker.db",
        ) as mock_db,
        patch(
            "openlist_ani.backend.worker.asyncio.sleep",
            side_effect=_cancel_sleep,
        ),
    ):
        mock_config.rss.interval_time = 999
        mock_config.rss.strict = False
        mock_config.rss.filter.exclude_patterns = []
        mock_config.rss.filter.exclude_fansub = []
        mock_config.rss.filter.exclude_quality = []
        mock_config.rss.filter.exclude_languages = []
        mock_config.rss.filter.model_copy = lambda deep=False: mock_config.rss.filter
        mock_config.rss.priority.fansub = []
        mock_config.rss.priority.languages = []
        mock_config.rss.priority.quality = []
        mock_config.rss.priority.field_order = []
        mock_config.rss.priority.model_copy = (
            lambda deep=False: mock_config.rss.priority
        )
        mock_config.openlist.rename_format = "{anime_name} S{season:02d}E{episode:02d}"
        mock_db.add_resource = AsyncMock()

        try:
            await RSSPollWorker(mock_rss, queue).run()
        except _StopPoll:
            pass

    queued = []
    while not queue.empty():
        queued.append(queue.get_nowait())
    return queued, mock_parse


class TestPollRssFeeds:
    async def test_single_entry_parsed_and_queued(self):
        entry = _make_resource(title="Anime - 01")
        queued, mock_parse = await _run_poll_once([entry], [_parse_ok()])

        mock_parse.assert_awaited_once()
        assert len(queued) == 1
        assert queued[0].anime_name == "Anime"

    async def test_metadata_failure_skips_entry(self):
        entries = [_make_resource(title="A - 01"), _make_resource(title="B - 02")]
        queued, _ = await _run_poll_once(
            entries,
            [_parse_fail("parse error"), _parse_ok(anime_name="B", episode=2)],
        )

        assert entries[0].anime_name is None
        assert len(queued) == 1
        assert queued[0].anime_name == "B"

    async def test_fansub_from_llm_when_website_has_none(self):
        entry = _make_resource()
        assert entry.fansub is None
        queued, _ = await _run_poll_once([entry], [_parse_ok(fansub="LLM_SubGroup")])

        assert len(queued) == 1
        assert queued[0].fansub == "LLM_SubGroup"

    async def test_fansub_from_website_overrides_llm(self):
        entry = _make_resource()
        entry.fansub = "Mikan_SubGroup"
        queued, _ = await _run_poll_once([entry], [_parse_ok(fansub="LLM_SubGroup")])

        assert len(queued) == 1
        assert queued[0].fansub == "Mikan_SubGroup"


class TestDownloadDispatchWorker:
    async def test_single_entry_dispatched(self):
        entry = _make_resource(title="Anime - 01")
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(queue, mock_manager)

        mock_manager.download.assert_awaited_once()

    async def test_skip_already_downloading(self):
        entry = _make_resource(title="Dup - 01")
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: True

        await _run_dispatch_once(queue, mock_manager)

        mock_manager.download.assert_not_awaited()

    async def test_multiple_entries_batched(self):
        entries = [_make_resource(title=f"Anime - {i:02d}") for i in range(3)]
        queue: asyncio.Queue = asyncio.Queue()
        for e in entries:
            await queue.put(e)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(queue, mock_manager)

        assert mock_manager.download.await_count == 3

    async def test_none_season_episode_dispatched(self):
        """Entries with None season/episode should still be dispatched."""
        entry = _make_resource(title="Anime - SP")
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(queue, mock_manager)

        mock_manager.download.assert_awaited_once()
