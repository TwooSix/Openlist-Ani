"""Tests for MikanWebsite entry parsing and season extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from openlist_ani.core.website.mikan import MikanWebsite


@pytest.fixture
def mikan():
    return MikanWebsite()


def _build_mikan_html(
    anime_name: str = "我推的孩子 第二季",
    fansub: str = "喵萌奶茶屋",
) -> str:
    """Build a minimal Mikan details page HTML with expected CSS selectors."""
    return f"""
    <html><body>
        <p class="bangumi-title">
            <a class="w-other-c">{anime_name}</a>
        </p>
        <p class="bangumi-info">
            <a class="magnet-link-wrap">{fansub}</a>
        </p>
    </body></html>
    """


def _make_mikan_entry(
    title: str = "[喵萌] 我推的孩子 第二季 - 01 [1080p]",
    magnet: str = "magnet:?xt=urn:btih:deadbeef",
    link: str = "https://mikanani.me/Home/Episode/123",
) -> SimpleNamespace:
    """Build a feedparser-like entry for Mikan with enclosure and web link."""
    entry = SimpleNamespace(title=title, link=link)
    enclosures = [{"href": magnet, "type": "application/x-bittorrent"}]
    entry.get = (
        lambda key, default=None: enclosures
        if key == "enclosures"
        else default
    )
    return entry


class TestMikanWebsite:
    def test_split_name_season_basic(self, mikan):
        name, season = mikan._split_anime_name_and_season("我推的孩子 第二季")
        assert name == "我推的孩子"
        assert season == 2

    def test_split_name_season_no_season(self, mikan):
        name, season = mikan._split_anime_name_and_season("我独自升级")
        assert name == "我独自升级"
        assert season == 1

    def test_split_name_season_empty_string(self, mikan):
        """Empty string must not crash."""
        name, season = mikan._split_anime_name_and_season("")
        assert name == ""
        assert season == 1

    def test_split_name_season_none_input(self, mikan):
        """None input must not crash (coredump prevention)."""
        name, season = mikan._split_anime_name_and_season(None)
        assert name == ""
        assert season == 1

    def test_split_name_season_bufen_token(self, mikan):
        """Season token '部分' should be recognized."""
        name, season = mikan._split_anime_name_and_season("进击的巨人 第二部分")
        assert name == "进击的巨人"
        assert season == 2

    def test_split_name_season_bu_token(self, mikan):
        """Season token '部' (without '分') should be recognized."""
        name, season = mikan._split_anime_name_and_season("鬼灭之刃 第三部")
        assert name == "鬼灭之刃"
        assert season == 3

    def test_split_name_season_bufen_without_di(self, mikan):
        """'部分' token without leading '第' should still match."""
        name, season = mikan._split_anime_name_and_season("某动画 二部分")
        assert name == "某动画"
        assert season == 2

    def test_parse_cn_number_arabic(self, mikan):
        assert mikan._parse_cn_number("3") == 3

    def test_parse_cn_number_chinese(self, mikan):
        assert mikan._parse_cn_number("三") == 3
        assert mikan._parse_cn_number("十") == 10
        assert mikan._parse_cn_number("十二") == 12
        assert mikan._parse_cn_number("二十") == 20

    def test_parse_cn_number_compound_tens_and_units(self, mikan):
        """Compound numbers like 二十一 (21) should be parsed correctly."""
        assert mikan._parse_cn_number("二十一") == 21
        assert mikan._parse_cn_number("三十五") == 35
        assert mikan._parse_cn_number("九十九") == 99

    async def test_parse_entry_no_title_returns_none(self, mikan):
        entry = SimpleNamespace(title=None, link="https://mikanani.me/page")
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await mikan.parse_entry(entry, session)
        assert result is None

    async def test_parse_entry_no_download_url_returns_none(self, mikan):
        entry = SimpleNamespace(title="Test", link="https://mikanani.me/page")
        entry.get = lambda key, default=None: [] if key == "enclosures" else default
        session = MagicMock()
        result = await mikan.parse_entry(entry, session)
        assert result is None

    async def test_parse_entry_non_web_link_returns_none(self, mikan):
        """Mikan requires a valid web page link for metadata fetching."""
        entry = SimpleNamespace(title="Test", link="magnet:?xt=urn:btih:abc")
        entry.get = lambda key, default=None: (
            [{"href": "magnet:?xt=urn:btih:abc", "type": "application/x-bittorrent"}]
            if key == "enclosures"
            else default
        )
        session = MagicMock()
        result = await mikan.parse_entry(entry, session)
        assert result is None

    async def test_fetch_metadata_happy_path(self, mikan):
        """_fetch_metadata returns correct anime_name, season, fansub."""
        html = _build_mikan_html(
            anime_name="我推的孩子 第二季",
            fansub="喵萌奶茶屋",
        )
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=html)

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        ctx_manager = AsyncMock()
        ctx_manager.__aenter__.return_value = mock_response
        ctx_manager.__aexit__.return_value = False
        mock_session.get.return_value = ctx_manager

        metadata = await mikan._fetch_metadata(
            mock_session, "https://mikanani.me/Home/Episode/123"
        )
        assert metadata["anime_name"] == "我推的孩子"
        assert metadata["season"] == 2
        assert metadata["fansub"] == "喵萌奶茶屋"

    async def test_fetch_metadata_http_404(self, mikan):
        """_fetch_metadata returns empty metadata dict on HTTP 404."""
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.text = AsyncMock(return_value="Not Found")

        mock_session = MagicMock(spec=aiohttp.ClientSession)
        ctx_manager = AsyncMock()
        ctx_manager.__aenter__.return_value = mock_response
        ctx_manager.__aexit__.return_value = False
        mock_session.get.return_value = ctx_manager

        metadata = await mikan._fetch_metadata(
            mock_session, "https://mikanani.me/bad-url"
        )
        assert metadata["anime_name"] is None
        assert metadata["season"] is None
        assert metadata["fansub"] is None

    async def test_fetch_metadata_timeout(self, mikan):
        """_fetch_metadata returns empty metadata dict on timeout."""
        mock_session = MagicMock(spec=aiohttp.ClientSession)
        ctx_manager = AsyncMock()
        ctx_manager.__aenter__.side_effect = TimeoutError("request timed out")
        ctx_manager.__aexit__.return_value = False
        mock_session.get.return_value = ctx_manager

        metadata = await mikan._fetch_metadata(
            mock_session, "https://mikanani.me/slow"
        )
        assert metadata["anime_name"] is None
        assert metadata["season"] is None
        assert metadata["fansub"] is None

    async def test_parse_entry_happy_path(self, mikan):
        """parse_entry returns AnimeResourceInfo with metadata populated."""
        entry = _make_mikan_entry(
            title="[喵萌] 我推的孩子 第二季 - 01 [1080p]",
            magnet="magnet:?xt=urn:btih:deadbeef",
            link="https://mikanani.me/Home/Episode/123",
        )
        fake_metadata = {
            "anime_name": "我推的孩子",
            "season": 2,
            "fansub": "喵萌奶茶屋",
        }

        with patch.object(
            mikan, "_fetch_metadata", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = fake_metadata
            session = MagicMock(spec=aiohttp.ClientSession)
            result = await mikan.parse_entry(entry, session)

        assert result is not None
        assert result.title == "[喵萌] 我推的孩子 第二季 - 01 [1080p]"
        assert result.download_url == "magnet:?xt=urn:btih:deadbeef"
        assert result.anime_name == "我推的孩子"
        assert result.season == 2
        assert result.fansub == "喵萌奶茶屋"
