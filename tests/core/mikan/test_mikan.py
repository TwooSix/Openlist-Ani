"""
Unit tests for the Mikan client and LLM tools.

Uses mock HTTP responses to verify login, subscription, and search
functionality without requiring a real Mikan account.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openlist_ani.core.mikan.client import MikanClient

_MOCK_CREDENTIAL = "mock-test-credential"

# ---- Sample HTML fixtures ----

LOGIN_PAGE_HTML = """
<html>
<body>
<form action="/Account/Login?ReturnUrl=%2F" method="post" id="loginForm">
    <input type="text" name="UserName" />
    <input type="password" name="Password" />
    <input type="checkbox" value="true" name="RememberMe">
    <button type="submit">登录</button>
    <input name="__RequestVerificationToken" type="hidden"
           value="test-csrf-token-12345" />
</form>
</body>
</html>
"""

LOGIN_SUCCESS_HTML = """
<html>
<body>
<div id="sk-header">Welcome home page</div>
</body>
</html>
"""

LOGIN_FAIL_HTML = """
<html>
<body>
<form action="/Account/Login" method="post">
    <button id="login-popover-submit" type="submit">登录</button>
    <input name="__RequestVerificationToken" type="hidden"
           value="another-token" />
</form>
</body>
</html>
"""

SEARCH_RESULTS_HTML = """
<html>
<body>
<div class="bangumi-list">
    <a href="/Home/Bangumi/3824">黄金神威 最终章</a>
    <a href="/Home/Bangumi/3826">能帮我弄干净吗？</a>
    <a href="/Home/Bangumi/3824">黄金神威 最终章</a>
</div>
</body>
</html>
"""

EMPTY_SEARCH_HTML = """
<html>
<body>
<div class="bangumi-list"></div>
</body>
</html>
"""

BANGUMI_PAGE_HTML = """
<html>
<body>
<div class="header">字幕组列表</div>
<ul class="list-unstyled">
    <li class="leftbar-item">
        <span>
            <a class="subgroup-name subgroup-1210"
               data-anchor="#1210">黑白字幕组</a>
        </span>
    </li>
    <li class="leftbar-item">
        <span>
            <a class="subgroup-name subgroup-1243"
               data-anchor="#1243">六四位元字幕组</a>
        </span>
    </li>
    <li class="leftbar-item">
        <span>
            <a class="subgroup-name subgroup-615"
               data-anchor="#615">Kirara Fantasia</a>
        </span>
    </li>
</ul>
</body>
</html>
"""


# ---- Helper to create mock response ----


def _make_mock_response(
    status: int = 200,
    text: str = "",
    url: str = "https://mikanani.me/",
) -> MagicMock:
    """Create a mock aiohttp response for testing."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.url = url
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


# ---- Tests ----


class TestMikanClientInit:
    """Test MikanClient initialization."""

    def test_init(self):
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)
        assert client._username == "user"
        assert client._password == _MOCK_CREDENTIAL
        assert not client.is_authenticated

    def test_init_empty_credentials(self):
        client = MikanClient(username="", password="")
        assert client._username == ""
        assert client._password == ""


class TestMikanClientLogin:
    """Test MikanClient login flow."""

    @pytest.mark.asyncio
    async def test_login_missing_credentials(self):
        """Login should fail when credentials are empty."""
        client = MikanClient(username="", password="")
        result = await client.login()
        assert result is False
        assert not client.is_authenticated

    @pytest.mark.asyncio
    async def test_login_success(self):
        """Login should succeed when server redirects to home page."""
        client = MikanClient(username="testuser", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()

        # Mock GET for CSRF token
        mock_get_resp = _make_mock_response(status=200, text=LOGIN_PAGE_HTML)
        mock_session.get = MagicMock(return_value=mock_get_resp)

        # Mock POST for login - success redirects to home
        mock_post_resp = _make_mock_response(
            status=200,
            text=LOGIN_SUCCESS_HTML,
            url="https://mikanani.me/",
        )
        mock_session.post = MagicMock(return_value=mock_post_resp)
        mock_session.closed = False

        client._session = mock_session

        result = await client.login()
        assert result is True
        assert client.is_authenticated

    @pytest.mark.asyncio
    async def test_login_failure(self):
        """Login should fail when staying on login page."""
        client = MikanClient(username="testuser", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()

        mock_get_resp = _make_mock_response(status=200, text=LOGIN_PAGE_HTML)
        mock_session.get = MagicMock(return_value=mock_get_resp)

        mock_post_resp = _make_mock_response(
            status=200,
            text=LOGIN_FAIL_HTML,
            url="https://mikanani.me/Account/Login?ReturnUrl=%2F",
        )
        mock_session.post = MagicMock(return_value=mock_post_resp)
        mock_session.closed = False

        client._session = mock_session

        result = await client.login()
        assert result is False
        assert not client.is_authenticated

    @pytest.mark.asyncio
    async def test_login_csrf_token_not_found(self):
        """Login should fail when CSRF token is not in page."""
        client = MikanClient(username="testuser", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_get_resp = _make_mock_response(
            status=200,
            text="<html><body>No token here</body></html>",
        )
        mock_session.get = MagicMock(return_value=mock_get_resp)
        mock_session.closed = False

        client._session = mock_session

        result = await client.login()
        assert result is False


class TestMikanClientSubscribe:
    """Test MikanClient subscribe/unsubscribe."""

    @pytest.mark.asyncio
    async def test_subscribe_success(self):
        """Subscribe should succeed on 200 response."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)
        client._authenticated = True

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=200, text="")
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        result = await client.subscribe_bangumi(bangumi_id=3824)
        assert result is True

        # Verify POST was called with correct data
        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args
        assert call_kwargs[1]["json"]["BangumiID"] == 3824

    @pytest.mark.asyncio
    async def test_subscribe_with_subgroup_and_language(self):
        """Subscribe with subtitle group and language."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)
        client._authenticated = True

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=200, text="")
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        result = await client.subscribe_bangumi(
            bangumi_id=3824, subtitle_group_id=21, language=1
        )
        assert result is True

        call_kwargs = mock_session.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["BangumiID"] == 3824
        assert payload["SubtitleGroupID"] == 21
        assert payload["Language"] == 1

    @pytest.mark.asyncio
    async def test_subscribe_not_authenticated(self):
        """Subscribe should attempt login if not authenticated."""
        client = MikanClient(username="", password="")
        client._authenticated = False

        result = await client.subscribe_bangumi(bangumi_id=3824)
        assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_success(self):
        """Unsubscribe should succeed on 200 response."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)
        client._authenticated = True

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=200, text="")
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        result = await client.unsubscribe_bangumi(bangumi_id=3824)
        assert result is True


class TestMikanClientSearch:
    """Test MikanClient search functionality."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """Search should parse results from HTML."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=200, text=SEARCH_RESULTS_HTML)
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        results = await client.search_bangumi("黄金")
        assert len(results) == 2  # Deduplicated
        assert results[0]["bangumi_id"] == 3824
        assert results[0]["name"] == "黄金神威 最终章"
        assert results[1]["bangumi_id"] == 3826

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """Search should return empty list when no matches."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=200, text=EMPTY_SEARCH_HTML)
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        results = await client.search_bangumi("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self):
        """Search should return empty list on HTTP error."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=500, text="Internal Server Error")
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        results = await client.search_bangumi("test")
        assert results == []


class TestParseSearchResults:
    """Test the static HTML parser for search results."""

    def test_parse_empty_html(self):
        results = MikanClient._parse_search_results("<html></html>")
        assert results == []

    def test_parse_with_duplicates(self):
        html = """
        <a href="/Home/Bangumi/100">Anime A</a>
        <a href="/Home/Bangumi/200">Anime B</a>
        <a href="/Home/Bangumi/100">Anime A again</a>
        """
        results = MikanClient._parse_search_results(html)
        assert len(results) == 2
        assert results[0]["bangumi_id"] == 100
        assert results[1]["bangumi_id"] == 200

    def test_parse_no_matching_links(self):
        html = '<a href="/other/page">Some link</a>'
        results = MikanClient._parse_search_results(html)
        assert results == []


class TestFetchBangumiSubgroups:
    """Test MikanClient subtitle group fetching."""

    @pytest.mark.asyncio
    async def test_fetch_subgroups(self):
        """Should parse subtitle groups from bangumi page HTML."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=200, text=BANGUMI_PAGE_HTML)
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        subgroups = await client.fetch_bangumi_subgroups(3826)
        assert len(subgroups) == 3
        assert subgroups[0] == {"id": 1210, "name": "黑白字幕组", "releases": []}
        assert subgroups[1] == {"id": 1243, "name": "六四位元字幕组", "releases": []}
        assert subgroups[2] == {"id": 615, "name": "Kirara Fantasia", "releases": []}

    @pytest.mark.asyncio
    async def test_fetch_subgroups_empty(self):
        """Should return empty list when page has no subtitle groups."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_resp = _make_mock_response(
            status=200, text="<html><body>No groups</body></html>"
        )
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        subgroups = await client.fetch_bangumi_subgroups(9999)
        assert subgroups == []

    @pytest.mark.asyncio
    async def test_fetch_subgroups_http_error(self):
        """Should return empty list on HTTP error."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_resp = _make_mock_response(status=404, text="Not Found")
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.closed = False

        client._session = mock_session

        subgroups = await client.fetch_bangumi_subgroups(9999)
        assert subgroups == []


class TestParseSubgroups:
    """Test the static HTML parser for subtitle groups."""

    def test_parse_subgroups(self):
        subgroups = MikanClient._parse_subgroups(BANGUMI_PAGE_HTML)
        assert len(subgroups) == 3
        assert subgroups[0]["id"] == 1210
        assert subgroups[0]["name"] == "黑白字幕组"

    def test_parse_subgroups_empty_html(self):
        subgroups = MikanClient._parse_subgroups("<html></html>")
        assert subgroups == []

    def test_parse_subgroups_deduplicates(self):
        html = """
        <a class="subgroup-name subgroup-100" data-anchor="#100">Group A</a>
        <a class="subgroup-name subgroup-100" data-anchor="#100">Group A dup</a>
        <a class="subgroup-name subgroup-200" data-anchor="#200">Group B</a>
        """
        subgroups = MikanClient._parse_subgroups(html)
        assert len(subgroups) == 2
        assert subgroups[0]["id"] == 100
        assert subgroups[1]["id"] == 200


class TestMikanEnsureAuthenticated:
    """Test _ensure_authenticated auto-login flow."""

    @pytest.mark.asyncio
    async def test_auto_login_when_not_authenticated(self):
        """_ensure_authenticated should trigger login() if not yet logged in."""
        client = MikanClient(username="testuser", password=_MOCK_CREDENTIAL)
        assert not client.is_authenticated

        mock_session = MagicMock()
        mock_session.closed = False

        # Mock GET for CSRF token page
        mock_get_resp = _make_mock_response(status=200, text=LOGIN_PAGE_HTML)
        mock_session.get = MagicMock(return_value=mock_get_resp)

        # Mock POST for login — success (redirect to home)
        mock_post_resp = _make_mock_response(
            status=200,
            text=LOGIN_SUCCESS_HTML,
            url="https://mikanani.me/",
        )
        mock_session.post = MagicMock(return_value=mock_post_resp)

        client._session = mock_session

        result = await client._ensure_authenticated()
        assert result is True
        assert client.is_authenticated

        # Verify login was actually performed (POST was called)
        mock_session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_login_when_already_authenticated(self):
        """_ensure_authenticated should return True without calling login()."""
        client = MikanClient(username="testuser", password=_MOCK_CREDENTIAL)
        client._authenticated = True

        # No session needed — login should not be called
        result = await client._ensure_authenticated()
        assert result is True

    @pytest.mark.asyncio
    async def test_auto_login_failure_returns_false(self):
        """_ensure_authenticated should return False when login fails."""
        client = MikanClient(username="baduser", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_session.closed = False

        mock_get_resp = _make_mock_response(status=200, text=LOGIN_PAGE_HTML)
        mock_session.get = MagicMock(return_value=mock_get_resp)

        mock_post_resp = _make_mock_response(
            status=200,
            text=LOGIN_FAIL_HTML,
            url="https://mikanani.me/Account/Login?ReturnUrl=%2F",
        )
        mock_session.post = MagicMock(return_value=mock_post_resp)

        client._session = mock_session

        result = await client._ensure_authenticated()
        assert result is False
        assert not client.is_authenticated


class TestMikanClientClose:
    """Test MikanClient.close() session cleanup."""

    @pytest.mark.asyncio
    async def test_close_cleans_up_session(self):
        """close() should close the session and reset auth state."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        client._session = mock_session
        client._authenticated = True

        await client.close()

        mock_session.close.assert_called_once()
        assert client._session is None
        assert not client.is_authenticated

    @pytest.mark.asyncio
    async def test_close_noop_when_no_session(self):
        """close() should be safe to call when no session exists."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)
        assert client._session is None

        # Should not raise
        await client.close()
        assert client._session is None
        assert not client.is_authenticated

    @pytest.mark.asyncio
    async def test_close_noop_when_session_already_closed(self):
        """close() should be safe to call when session is already closed."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_session.closed = True  # Already closed
        mock_session.close = AsyncMock()

        client._session = mock_session
        client._authenticated = True

        await client.close()

        # Should not call close on an already-closed session
        mock_session.close.assert_not_called()
        # But auth state should still be reset
        assert not client.is_authenticated


class TestMikanCsrfFallback:
    """Test CSRF token extraction with regex failure and BS4 fallback."""

    @pytest.mark.asyncio
    async def test_csrf_regex_fallback_to_beautifulsoup(self):
        """When regex fails to match, BeautifulSoup should find the token."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        # HTML where the attribute order differs from the regex pattern,
        # so the regex won't match, but BS4 will find the input by name.
        tricky_html = """
        <html>
        <body>
        <form>
            <input value="bs4-found-token"
                   name="__RequestVerificationToken"
                   data-extra="something"
                   type="hidden" />
        </form>
        </body>
        </html>
        """

        mock_session = MagicMock()
        mock_session.closed = False
        mock_get_resp = _make_mock_response(status=200, text=tricky_html)
        mock_session.get = MagicMock(return_value=mock_get_resp)

        client._session = mock_session

        token = await client._fetch_csrf_token("https://mikanani.me/Account/Login")
        assert token == "bs4-found-token"

    @pytest.mark.asyncio
    async def test_csrf_not_found_returns_none(self):
        """When neither regex nor BS4 finds the token, return None."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        no_token_html = "<html><body><form>No token here</form></body></html>"

        mock_session = MagicMock()
        mock_session.closed = False
        mock_get_resp = _make_mock_response(status=200, text=no_token_html)
        mock_session.get = MagicMock(return_value=mock_get_resp)

        client._session = mock_session

        token = await client._fetch_csrf_token("https://mikanani.me/Account/Login")
        assert token is None

    @pytest.mark.asyncio
    async def test_csrf_non_200_returns_none(self):
        """When the CSRF page returns non-200, return None."""
        client = MikanClient(username="user", password=_MOCK_CREDENTIAL)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_get_resp = _make_mock_response(status=500, text="Error")
        mock_session.get = MagicMock(return_value=mock_get_resp)

        client._session = mock_session

        token = await client._fetch_csrf_token("https://mikanani.me/Account/Login")
        assert token is None
