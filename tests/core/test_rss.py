"""Tests for RSSManager.check_update logic."""

from unittest.mock import AsyncMock, MagicMock, patch

from openlist_ani.core.website.model import AnimeResourceInfo


class TestRSSManagerCheckUpdate:
    """Test RSSManager.check_update with mocked dependencies."""

    async def test_empty_urls_returns_empty(self):
        """No configured URLs → empty result, no crash."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mgr = RSSManager(download_manager=mock_dm)

        with patch("openlist_ani.core.rss.manager.config") as mock_config:
            mock_config.rss.urls = []
            result = await mgr.check_update()

        assert result == []

    async def test_new_entries_returned(self):
        """New entries that are not downloaded should be returned."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mock_dm.is_downloading = MagicMock(return_value=False)
        mgr = RSSManager(download_manager=mock_dm)

        resource = AnimeResourceInfo(
            title="New Anime - 01",
            download_url="magnet:?xt=urn:btih:new",
        )

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(return_value=[resource])

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db") as mock_db,
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://acg.rip/.xml"]
            mock_db.is_downloaded = AsyncMock(return_value=False)

            result = await mgr.check_update()

        assert len(result) == 1
        assert result[0].title == "New Anime - 01"

    async def test_already_downloaded_filtered(self):
        """Entries already in DB should be filtered out."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mock_dm.is_downloading = MagicMock(return_value=False)
        mgr = RSSManager(download_manager=mock_dm)

        resource = AnimeResourceInfo(
            title="Old Anime - 01",
            download_url="magnet:?xt=urn:btih:old",
        )

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(return_value=[resource])

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db") as mock_db,
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://acg.rip/.xml"]
            mock_db.is_downloaded = AsyncMock(return_value=True)

            result = await mgr.check_update()

        assert len(result) == 0

    async def test_currently_downloading_filtered(self):
        """Entries currently being downloaded should be filtered out."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mock_dm.is_downloading = MagicMock(return_value=True)
        mgr = RSSManager(download_manager=mock_dm)

        resource = AnimeResourceInfo(
            title="Active Anime - 01",
            download_url="magnet:?xt=urn:btih:active",
        )

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(return_value=[resource])

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db") as mock_db,
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://acg.rip/.xml"]
            mock_db.is_downloaded = AsyncMock(return_value=False)

            result = await mgr.check_update()

        assert len(result) == 0

    async def test_entry_without_download_url_skipped(self):
        """Entries with empty download_url must be silently skipped."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mock_dm.is_downloading = MagicMock(return_value=False)
        mgr = RSSManager(download_manager=mock_dm)

        resource_no_url = AnimeResourceInfo(title="No URL Anime", download_url="")
        resource_good = AnimeResourceInfo(
            title="Good Anime - 01",
            download_url="magnet:?xt=urn:btih:good",
        )

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(
            return_value=[resource_no_url, resource_good]
        )

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db") as mock_db,
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://acg.rip/.xml"]
            mock_db.is_downloaded = AsyncMock(return_value=False)

            result = await mgr.check_update()

        assert len(result) == 1
        assert result[0].title == "Good Anime - 01"

    async def test_fetch_exception_does_not_crash(self):
        """A handler raising an exception must not crash the whole check."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mgr = RSSManager(download_manager=mock_dm)

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(side_effect=Exception("Network error"))

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db"),
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://fail.example.com/rss"]
            result = await mgr.check_update()

        assert result == []

    async def test_handler_returns_non_list_does_not_crash(self):
        """If a handler returns unexpected type, it should be handled gracefully."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mgr = RSSManager(download_manager=mock_dm)

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(return_value="unexpected string")

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db"),
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://example.com/rss"]
            result = await mgr.check_update()

        assert result == []

    async def test_no_handler_for_url_skipped(self):
        """When _get_website_handler returns None, URL should be skipped."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mgr = RSSManager(download_manager=mock_dm)

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db"),
            patch.object(mgr, "_get_website_handler", return_value=None),
        ):
            mock_config.rss.urls = ["https://unknown.example.com/rss"]
            result = await mgr.check_update()

        assert result == []

    async def test_multiple_feeds_merged(self):
        """Entries from multiple feeds should be merged into one list."""
        from openlist_ani.core.rss import RSSManager

        mock_dm = MagicMock()
        mock_dm.is_downloading = MagicMock(return_value=False)
        mgr = RSSManager(download_manager=mock_dm)

        r1 = AnimeResourceInfo(title="Anime A - 01", download_url="magnet:?a")
        r2 = AnimeResourceInfo(title="Anime B - 01", download_url="magnet:?b")

        mock_handler = AsyncMock()
        mock_handler.fetch_feed = AsyncMock(side_effect=[[r1], [r2]])

        with (
            patch("openlist_ani.core.rss.manager.config") as mock_config,
            patch("openlist_ani.core.rss.manager.db") as mock_db,
            patch.object(mgr, "_get_website_handler", return_value=mock_handler),
        ):
            mock_config.rss.urls = ["https://a.com/rss", "https://b.com/rss"]
            mock_db.is_downloaded = AsyncMock(return_value=False)

            result = await mgr.check_update()

        assert len(result) == 2
        titles = {r.title for r in result}
        assert "Anime A - 01" in titles
        assert "Anime B - 01" in titles
