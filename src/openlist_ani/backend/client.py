"""
HTTP client for communicating with the backend API.

Used by the assistant module to interact with the backend service
instead of directly managing downloads.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from ..logger import logger


class BackendClient:
    """Async HTTP client for the backend API."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300),
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request to the backend.

        Args:
            method: HTTP method.
            path: API path (e.g. "/api/downloads").
            json: Optional JSON body.

        Returns:
            Parsed JSON response.

        Raises:
            aiohttp.ClientError: On connection failure.
            RuntimeError: On non-2xx response.
        """
        url = f"{self._base_url}{path}"
        session = await self._get_session()
        async with session.request(method, url, json=json) as resp:
            body = await resp.json()
            if resp.status >= 400:
                detail = body.get("detail", str(body))
                raise RuntimeError(f"Backend API error ({resp.status}): {detail}")
            return body

    # ── RSS ──────────────────────────────────────────────────────────

    async def add_rss_url(self, url: str) -> dict[str, Any]:
        """Add an RSS monitoring URL.

        Args:
            url: RSS feed URL.

        Returns:
            API response dict with success, message, urls.
        """
        logger.debug(f"BackendClient: Adding RSS URL: {url}")
        return await self._request("POST", "/api/rss", json={"url": url})

    # ── Downloads ────────────────────────────────────────────────────

    async def create_download(
        self,
        download_url: str,
        title: str,
    ) -> dict[str, Any]:
        """Create a new download task.

        Args:
            download_url: Magnet/torrent URL.
            title: Resource title.

        Returns:
            API response dict with success, message, task.
        """
        logger.debug(f"BackendClient: Creating download: {title}")
        return await self._request(
            "POST",
            "/api/downloads",
            json={"download_url": download_url, "title": title},
        )

    async def list_downloads(self) -> dict[str, Any]:
        """Get all active download tasks.

        Returns:
            API response dict with tasks list and total count.
        """
        return await self._request("GET", "/api/downloads")

    async def get_download(self, task_id: str) -> dict[str, Any]:
        """Get a specific download task's status.

        Args:
            task_id: Task UUID.

        Returns:
            API response dict with task details.
        """
        return await self._request("GET", f"/api/downloads/{task_id}")

    async def restart(self) -> dict[str, Any]:
        """Request service restart.

        Returns:
            API response dict.
        """
        logger.info("BackendClient: Requesting restart")
        return await self._request("POST", "/api/restart")
