"""Tests for worker module (batch dispatch)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openlist_ani.core.website.model import AnimeResourceInfo, VideoQuality


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


async def _run_dispatch_once(queue, mock_manager, batch_return_value):
    """Run dispatch_downloads until it processes one batch, then cancel."""
    from openlist_ani.backend.worker import dispatch_downloads

    active: set[asyncio.Task] = set()

    with (
        patch(
            "openlist_ani.backend.worker.parse_metadata",
            new_callable=AsyncMock,
            return_value=batch_return_value,
        ) as mock_parse,
        patch("openlist_ani.backend.worker.config") as mock_config,
    ):
        mock_config.openlist.download_path = "/downloads"

        try:
            async with asyncio.timeout(0.1):
                await dispatch_downloads(mock_manager, queue, active)
        except TimeoutError:
            pass

    return mock_parse


class TestDownloadDispatchWorker:
    async def test_single_entry_dispatched(self):
        entry = _make_resource(title="Anime - 01")
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        mock_parse = await _run_dispatch_once(queue, mock_manager, [_parse_ok()])

        mock_parse.assert_awaited_once()
        assert entry.anime_name == "Anime"

    async def test_skip_already_downloading(self):
        entry = _make_resource(title="Dup - 01")
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: True

        from openlist_ani.backend.worker import dispatch_downloads

        active: set[asyncio.Task] = set()
        with patch(
            "openlist_ani.backend.worker.parse_metadata",
            new_callable=AsyncMock,
        ) as mock_parse:
            try:
                async with asyncio.timeout(0.1):
                    await dispatch_downloads(mock_manager, queue, active)
            except TimeoutError:
                pass

        mock_parse.assert_not_awaited()

    async def test_multiple_entries_batched(self):
        entries = [_make_resource(title=f"Anime - {i:02d}") for i in range(3)]
        queue: asyncio.Queue = asyncio.Queue()
        for e in entries:
            await queue.put(e)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        results = [_parse_ok(episode=i) for i in range(3)]
        mock_parse = await _run_dispatch_once(queue, mock_manager, results)

        mock_parse.assert_awaited_once()
        assert len(mock_parse.call_args.args[0]) == 3

    async def test_metadata_failure_skips_entry(self):
        entries = [_make_resource(title="A - 01"), _make_resource(title="B - 02")]
        queue: asyncio.Queue = asyncio.Queue()
        for e in entries:
            await queue.put(e)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(
            queue,
            mock_manager,
            [_parse_fail("parse error"), _parse_ok(anime_name="B", episode=2)],
        )

        assert entries[0].anime_name is None
        assert entries[1].anime_name == "B"

    async def test_none_season_episode_formatting(self):
        entry = _make_resource(title="Anime - SP")
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(
            queue, mock_manager, [_parse_ok(season=None, episode=None)]
        )

        mock_manager.download.assert_awaited_once()

    # -----------------------------------------------------------------------
    # fansub priority logic
    # -----------------------------------------------------------------------

    async def test_fansub_from_llm_when_website_has_none(self):
        entry = _make_resource()
        assert entry.fansub is None
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(
            queue, mock_manager, [_parse_ok(fansub="LLM_SubGroup")]
        )

        assert entry.fansub == "LLM_SubGroup"

    async def test_fansub_from_website_overrides_llm(self):
        entry = _make_resource()
        entry.fansub = "Mikan_SubGroup"
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(
            queue, mock_manager, [_parse_ok(fansub="LLM_SubGroup")]
        )

        assert entry.fansub == "Mikan_SubGroup"

    async def test_fansub_from_website_preserved_when_llm_returns_none(self):
        entry = _make_resource()
        entry.fansub = "Mikan_SubGroup"
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(queue, mock_manager, [_parse_ok(fansub=None)])

        assert entry.fansub == "Mikan_SubGroup"

    async def test_fansub_remains_none_when_both_are_none(self):
        entry = _make_resource()
        assert entry.fansub is None
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(entry)

        mock_manager = AsyncMock()
        mock_manager.is_downloading = lambda e: False

        await _run_dispatch_once(queue, mock_manager, [_parse_ok(fansub=None)])

        assert entry.fansub is None
