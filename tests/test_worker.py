"""Tests for worker._download_entry edge cases."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openlist_ani.core.website.model import AnimeResourceInfo, VideoQuality


def _make_resource(
    title: str = "Test Anime - 01",
    download_url: str = "magnet:?xt=urn:btih:abc123",
) -> AnimeResourceInfo:
    return AnimeResourceInfo(title=title, download_url=download_url)


class TestDownloadEntry:
    """Test worker._download_entry edge cases."""

    async def test_metadata_parse_failure_does_not_crash(self):
        """When parse_metadata returns None, should skip gracefully."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource(title="Bad Anime - 01")
        mock_manager = AsyncMock()

        with patch(
            "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
        ) as mock_parse:
            mock_parse.return_value = None
            await _download_entry(mock_manager, entry)

        mock_manager.download.assert_not_awaited()

    async def test_metadata_parse_exception_does_not_crash(self):
        """Exception in parse_metadata should be caught."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource(title="Crash Anime - 01")
        mock_manager = AsyncMock()

        with patch(
            "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
        ) as mock_parse:
            mock_parse.side_effect = RuntimeError("parse boom")
            await _download_entry(mock_manager, entry)

        mock_manager.download.assert_not_awaited()

    async def test_none_season_episode_formatting(self):
        """When season/episode are None, formatting must not crash."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource(title="Anime - SP")
        mock_manager = AsyncMock()

        meta = SimpleNamespace(
            anime_name="Anime",
            season=None,
            episode=None,
            quality=VideoQuality.k1080p,
            fansub="SubGroup",
            languages=[],
            version=1,
        )

        with (
            patch(
                "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
            ) as mock_parse,
            patch("openlist_ani.worker.config") as mock_config,
        ):
            mock_parse.return_value = meta
            mock_config.openlist.download_path = "/downloads"
            await _download_entry(mock_manager, entry)

        mock_manager.download.assert_awaited_once()

    # -----------------------------------------------------------------------
    # fansub priority logic
    # -----------------------------------------------------------------------

    async def test_fansub_from_llm_when_website_has_none(self):
        """When entry.fansub is None (website didn't provide it), LLM result is used."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource()
        assert entry.fansub is None  # website produced no fansub

        mock_manager = AsyncMock()
        meta = SimpleNamespace(
            anime_name="Anime",
            season=1,
            episode=1,
            quality=VideoQuality.k1080p,
            fansub="LLM_SubGroup",
            languages=[],
            version=1,
        )

        with (
            patch(
                "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
            ) as mock_parse,
            patch("openlist_ani.worker.config") as mock_config,
        ):
            mock_parse.return_value = meta
            mock_config.openlist.download_path = "/downloads"
            await _download_entry(mock_manager, entry)

        assert entry.fansub == "LLM_SubGroup"
        mock_manager.download.assert_awaited_once()

    async def test_fansub_from_website_overrides_llm(self):
        """When entry.fansub is already set (e.g. from mikan), it must NOT be
        overwritten by the LLM result even if LLM returns a different value."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource()
        entry.fansub = "Mikan_SubGroup"  # simulates value parsed by mikan website

        mock_manager = AsyncMock()
        meta = SimpleNamespace(
            anime_name="Anime",
            season=1,
            episode=1,
            quality=VideoQuality.k1080p,
            fansub="LLM_SubGroup",  # LLM returns a different fansub
            languages=[],
            version=1,
        )

        with (
            patch(
                "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
            ) as mock_parse,
            patch("openlist_ani.worker.config") as mock_config,
        ):
            mock_parse.return_value = meta
            mock_config.openlist.download_path = "/downloads"
            await _download_entry(mock_manager, entry)

        assert entry.fansub == "Mikan_SubGroup"
        mock_manager.download.assert_awaited_once()

    async def test_fansub_from_website_preserved_when_llm_returns_none(self):
        """Website-parsed fansub is kept even when LLM returns None for fansub."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource()
        entry.fansub = "Mikan_SubGroup"

        mock_manager = AsyncMock()
        meta = SimpleNamespace(
            anime_name="Anime",
            season=1,
            episode=1,
            quality=VideoQuality.k1080p,
            fansub=None,  # LLM couldn't detect fansub
            languages=[],
            version=1,
        )

        with (
            patch(
                "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
            ) as mock_parse,
            patch("openlist_ani.worker.config") as mock_config,
        ):
            mock_parse.return_value = meta
            mock_config.openlist.download_path = "/downloads"
            await _download_entry(mock_manager, entry)

        assert entry.fansub == "Mikan_SubGroup"
        mock_manager.download.assert_awaited_once()

    async def test_fansub_remains_none_when_both_are_none(self):
        """When neither website nor LLM provides a fansub, entry.fansub stays None."""
        from openlist_ani.worker import _download_entry

        entry = _make_resource()
        assert entry.fansub is None

        mock_manager = AsyncMock()
        meta = SimpleNamespace(
            anime_name="Anime",
            season=1,
            episode=1,
            quality=VideoQuality.k1080p,
            fansub=None,
            languages=[],
            version=1,
        )

        with (
            patch(
                "openlist_ani.worker.parse_metadata", new_callable=AsyncMock
            ) as mock_parse,
            patch("openlist_ani.worker.config") as mock_config,
        ):
            mock_parse.return_value = meta
            mock_config.openlist.download_path = "/downloads"
            await _download_entry(mock_manager, entry)

        assert entry.fansub is None
        mock_manager.download.assert_awaited_once()
