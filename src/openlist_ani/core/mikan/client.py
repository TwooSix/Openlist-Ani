"""
Async HTTP client for Mikan (mikanani.me).

Handles cookie-based authentication (login) and anime subscription
management via the Mikan web API.
"""

from __future__ import annotations

import re
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from ...logger import logger

_MIKAN_BASE_URL = "https://mikanani.me"
_USER_AGENT = "openlist-ani/1.0 (https://github.com/Openlist-Ani)"
_LOGIN_PATH = "/Account/Login"
_SUBSCRIBE_PATH = "/Home/SubscribeBangumi"
_UNSUBSCRIBE_PATH = "/Home/UnsubscribeBangumi"
_CSRF_TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"'
    r"|"
    r'type="hidden"\s+name="__RequestVerificationToken"\s+value="([^"]+)"'
)


class MikanClient:
    """Async client for Mikan website API.

    Authenticates via username/password and manages cookie-based sessions
    for subscribing/unsubscribing to bangumi.

    Args:
        username: Mikan account username.
        password: Mikan account password.
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._authenticated = False

    def _ensure_session(self) -> aiohttp.ClientSession:
        """Create or return the shared aiohttp session with cookie jar."""
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(
                cookie_jar=jar,
                headers={
                    "User-Agent": _USER_AGENT,
                },
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._authenticated = False

    @property
    def is_authenticated(self) -> bool:
        """Whether the client has successfully logged in."""
        return self._authenticated

    async def _fetch_csrf_token(self, url: str) -> str | None:
        """Fetch a page and extract the __RequestVerificationToken.

        Args:
            url: Full URL of the page to fetch.

        Returns:
            CSRF token string, or None if not found.
        """
        session = self._ensure_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning(
                    f"Mikan: Failed to fetch CSRF token page (status={resp.status})"
                )
                return None
            html = await resp.text()

        # Try regex first (faster)
        match = _CSRF_TOKEN_RE.search(html)
        if match:
            return match.group(1) or match.group(2)

        # Fallback to BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        if token_input:
            return token_input.get("value", "")

        logger.warning("Mikan: CSRF token not found in page")
        return None

    async def login(self) -> bool:
        """Authenticate with Mikan using username and password.

        Performs a POST to /Account/Login with form data including
        the CSRF token obtained from the login page.

        Returns:
            True if login succeeded, False otherwise.
        """
        if not self._username or not self._password:
            logger.error("Mikan: Username or password not configured")
            return False

        login_url = f"{_MIKAN_BASE_URL}{_LOGIN_PATH}?ReturnUrl=%2F"
        csrf_token = await self._fetch_csrf_token(login_url)
        if not csrf_token:
            logger.error("Mikan: Could not obtain CSRF token for login")
            return False

        session = self._ensure_session()
        form_data = {
            "UserName": self._username,
            "Password": self._password,
            "RememberMe": "true",
            "__RequestVerificationToken": csrf_token,
        }

        try:
            async with session.post(
                login_url,
                data=form_data,
                allow_redirects=True,
            ) as resp:
                # Successful login redirects to home page (status 200 after
                # redirect). Failed login stays on login page.
                final_url = str(resp.url)
                html = await resp.text()

                # Check if we're redirected away from login page
                is_on_login = _LOGIN_PATH.lower() in final_url.lower()
                # Also check for login form presence in response
                has_login_form = "login-popover-submit" in html

                if not is_on_login or (not has_login_form and resp.status == 200):
                    self._authenticated = True
                    logger.info(f"Mikan: Successfully logged in as {self._username}")
                    return True

                logger.error(
                    f"Mikan: Login failed for user {self._username} "
                    f"(redirected to {final_url})"
                )
                return False

        except aiohttp.ClientError as exc:
            logger.error(f"Mikan: Login request failed: {exc}")
            return False

    async def _ensure_authenticated(self) -> bool:
        """Ensure the client is authenticated, logging in if needed.

        Returns:
            True if authenticated, False if login failed.
        """
        if self._authenticated:
            return True
        return await self.login()

    async def subscribe_bangumi(
        self,
        bangumi_id: int,
        subtitle_group_id: int | None = None,
        language: int | None = None,
    ) -> bool:
        """Subscribe to a bangumi on Mikan.

        Args:
            bangumi_id: Mikan bangumi ID.
            subtitle_group_id: Optional subtitle group ID to subscribe to
                a specific fansub. None for all groups.
            language: Optional language filter (0=all, 1=Simplified Chinese,
                2=Traditional Chinese). None defaults to all.

        Returns:
            True if subscription succeeded, False otherwise.
        """
        if not await self._ensure_authenticated():
            return False

        session = self._ensure_session()
        payload: dict[str, Any] = {"BangumiID": bangumi_id}
        if subtitle_group_id is not None:
            payload["SubtitleGroupID"] = subtitle_group_id
        if language is not None:
            payload["Language"] = language

        url = f"{_MIKAN_BASE_URL}{_SUBSCRIBE_PATH}"
        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            ) as resp:
                if resp.status == 200:
                    logger.info(
                        f"Mikan: Subscribed to bangumi {bangumi_id} "
                        f"(subgroup={subtitle_group_id}, lang={language})"
                    )
                    return True

                body = await resp.text()
                logger.error(
                    f"Mikan: Subscribe failed (status={resp.status}): {body[:200]}"
                )
                return False

        except aiohttp.ClientError as exc:
            logger.error(f"Mikan: Subscribe request failed: {exc}")
            return False

    async def unsubscribe_bangumi(
        self,
        bangumi_id: int,
        subtitle_group_id: int | None = None,
    ) -> bool:
        """Unsubscribe from a bangumi on Mikan.

        Args:
            bangumi_id: Mikan bangumi ID.
            subtitle_group_id: Optional subtitle group ID. None for all.

        Returns:
            True if unsubscription succeeded, False otherwise.
        """
        if not await self._ensure_authenticated():
            return False

        session = self._ensure_session()
        payload: dict[str, Any] = {"BangumiID": bangumi_id}
        if subtitle_group_id is not None:
            payload["SubtitleGroupID"] = subtitle_group_id

        url = f"{_MIKAN_BASE_URL}{_UNSUBSCRIBE_PATH}"
        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            ) as resp:
                if resp.status == 200:
                    logger.info(f"Mikan: Unsubscribed from bangumi {bangumi_id}")
                    return True

                body = await resp.text()
                logger.error(
                    f"Mikan: Unsubscribe failed (status={resp.status}): "
                    f"{body[:200]}"
                )
                return False

        except aiohttp.ClientError as exc:
            logger.error(f"Mikan: Unsubscribe request failed: {exc}")
            return False

    async def search_bangumi(self, keyword: str) -> list[dict[str, Any]]:
        """Search for bangumi on Mikan by keyword.

        Args:
            keyword: Search keyword.

        Returns:
            List of dicts with bangumi_id, name, and url.
        """
        session = self._ensure_session()
        url = f"{_MIKAN_BASE_URL}/Home/Search"
        try:
            async with session.get(url, params={"searchstr": keyword}) as resp:
                if resp.status != 200:
                    logger.warning(f"Mikan: Search failed (status={resp.status})")
                    return []
                html = await resp.text()
        except aiohttp.ClientError as exc:
            logger.error(f"Mikan: Search request failed: {exc}")
            return []

        return self._parse_search_results(html)

    async def fetch_bangumi_subgroups(self, bangumi_id: int) -> list[dict[str, Any]]:
        """Fetch available subtitle groups for a bangumi.

        Scrapes the bangumi detail page to extract the list of fansub
        groups that have released episodes for this bangumi.

        Args:
            bangumi_id: Mikan bangumi ID.

        Returns:
            List of dicts with ``id`` (int) and ``name`` (str) for each
            subtitle group.
        """
        session = self._ensure_session()
        url = f"{_MIKAN_BASE_URL}/Home/Bangumi/{bangumi_id}"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"Mikan: Failed to fetch bangumi {bangumi_id} "
                        f"page (status={resp.status})"
                    )
                    return []
                html = await resp.text()
        except aiohttp.ClientError as exc:
            logger.error(f"Mikan: Failed to fetch bangumi {bangumi_id} page: {exc}")
            return []

        return self._parse_subgroups(html)

    @staticmethod
    def _parse_subgroups(html: str) -> list[dict[str, Any]]:
        """Parse subtitle groups from a bangumi detail page.

        Args:
            html: Raw HTML of the bangumi page.

        Returns:
            List of dicts with ``id`` and ``name``.
        """
        soup = BeautifulSoup(html, "lxml")
        results: list[dict[str, Any]] = []
        seen: set[int] = set()

        for link in soup.select("a.subgroup-name[data-anchor]"):
            anchor = link.get("data-anchor", "")
            match = re.search(r"#(\d+)", anchor)
            if not match:
                continue
            group_id = int(match.group(1))
            name = link.get_text(strip=True)
            if group_id and name and group_id not in seen:
                seen.add(group_id)
                results.append({"id": group_id, "name": name})

        return results

    @staticmethod
    def _parse_search_results(html: str) -> list[dict[str, Any]]:
        """Parse search results HTML into structured data.

        Args:
            html: Raw HTML response from search page.

        Returns:
            List of dicts with bangumi_id, name, and url.
        """
        soup = BeautifulSoup(html, "lxml")
        results: list[dict[str, Any]] = []

        # Search results are in the bangumi list on the page
        for link in soup.select("a[href*='/Home/Bangumi/']"):
            href = link.get("href", "")
            match = re.search(r"/Home/Bangumi/(\d+)", href)
            if not match:
                continue
            bangumi_id = int(match.group(1))
            name = link.get_text(strip=True)
            if name and bangumi_id:
                results.append(
                    {
                        "bangumi_id": bangumi_id,
                        "name": name,
                        "url": f"{_MIKAN_BASE_URL}{href}",
                    }
                )

        # Deduplicate by bangumi_id
        seen: set[int] = set()
        unique: list[dict[str, Any]] = []
        for item in results:
            if item["bangumi_id"] not in seen:
                seen.add(item["bangumi_id"])
                unique.append(item)

        return unique
