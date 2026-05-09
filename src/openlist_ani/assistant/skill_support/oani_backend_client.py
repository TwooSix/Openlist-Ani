"""Async HTTP client used by bundled assistant skill scripts."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from loguru import logger


class BackendClient:
    """Small client for the OpenList-Ani backend HTTP API."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            await asyncio.sleep(0.250)

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        session = self._get_session()
        async with session.request(method, url, json=json) as resp:
            body = await resp.json()
            if resp.status >= 400:
                detail = body.get("detail", str(body))
                raise RuntimeError(f"Backend API error ({resp.status}): {detail}")
            return body

    async def add_rss_url(self, url: str) -> dict[str, Any]:
        logger.debug(f"BackendClient: Adding RSS URL: {url}")
        return await self._request("POST", "/api/rss", json={"url": url})

    async def create_download(
        self,
        download_url: str,
        title: str,
    ) -> dict[str, Any]:
        logger.debug(f"BackendClient: Creating download: {title}")
        return await self._request(
            "POST",
            "/api/downloads",
            json={"download_url": download_url, "title": title},
        )

    async def list_downloads(self) -> dict[str, Any]:
        return await self._request("GET", "/api/downloads")

    async def get_download(self, task_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/downloads/{task_id}")

    async def restart(self) -> dict[str, Any]:
        logger.debug("BackendClient: Requesting restart")
        return await self._request("POST", "/api/restart")

    async def parse_rss(
        self,
        url: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        logger.debug(f"BackendClient: Parsing RSS {url}")
        body: dict[str, Any] = {"url": url}
        if limit is not None:
            body["limit"] = limit
        return await self._request("POST", "/api/parse_rss", json=body)

    async def resolve_magnet(
        self,
        magnet: str,
        metadata_timeout: int = 30,
    ) -> dict[str, Any]:
        logger.debug("BackendClient: Resolving magnet")
        return await self._request(
            "POST",
            "/api/resolve_magnet",
            json={"magnet": magnet, "metadata_timeout": metadata_timeout},
        )

    async def resolve_torrent(self, url: str) -> dict[str, Any]:
        logger.debug("BackendClient: Resolving torrent file")
        return await self._request(
            "POST",
            "/api/resolve_torrent",
            json={"url": url},
        )
