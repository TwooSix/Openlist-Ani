import asyncio
import atexit
from typing import Any

import aiohttp

from .....config import config
from .....logger import logger
from .....utils.cache import ttl_cached


class TMDBClient:
    def __init__(self):
        self.base_url = "https://api.tmdb.org/3"
        self._timeout = aiohttp.ClientTimeout(total=30, connect=30, sock_read=30)
        self._session: aiohttp.ClientSession | None = None

    @property
    def api_key(self) -> str:
        return config.llm.tmdb_api_key

    async def search_tv_show(self, query: str) -> list[dict[str, Any]]:
        """Search for a TV show on TMDB.

        Args:
            query: Search query string

        Returns:
            List of search results
        """
        if not self.api_key:
            logger.warning("TMDB API key not set, skipping search.")
            return []

        url = f"{self.base_url}/search/tv"
        params = {
            "api_key": self.api_key,
            "query": query,
            "language": config.llm.tmdb_language,
            "include_adult": "true",
        }

        try:
            data = await self._request_json(url, params)
            return data.get("results", []) if data else []
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"TMDB search request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in TMDB search: {e}")
            return []

    async def get_tv_show_details(self, tmdb_id: int) -> dict[str, Any]:
        """Get detailed information for a TV show including seasons.

        Args:
            tmdb_id: TMDB TV show ID

        Returns:
            TV show details dictionary
        """
        if not self.api_key:
            logger.warning("TMDB API key not set")
            return {}

        url = f"{self.base_url}/tv/{tmdb_id}"
        params = {
            "api_key": self.api_key,
            "language": config.llm.tmdb_language,
        }

        try:
            return await self._request_json(url, params)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"TMDB details request failed: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error getting TMDB details: {e}")
            return {}

    async def get_season_episodes(
        self, tmdb_id: int, season_number: int
    ) -> list[dict[str, Any]]:
        """Get episode list for a specific season, including air dates.

        Args:
            tmdb_id: TMDB TV show ID
            season_number: Season number to fetch episodes for

        Returns:
            List of episode dicts with episode_number, name, air_date, etc.
        """
        if not self.api_key:
            logger.warning("TMDB API key not set")
            return []

        url = f"{self.base_url}/tv/{tmdb_id}/season/{season_number}"
        params = {
            "api_key": self.api_key,
            "language": config.llm.tmdb_language,
        }

        try:
            data = await self._request_json(url, params)
            return data.get("episodes", []) if data else []
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"TMDB season episodes request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting TMDB season episodes: {e}")
            return []

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, trust_env=True)
        return self._session

    async def _request_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_session()
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class CachedTMDBClient(TMDBClient):
    @ttl_cached(maxsize=256, ttl=3600, key=lambda query: query.strip().lower())
    async def search_tv_show(self, query: str) -> list[dict[str, Any]]:
        return await super().search_tv_show(query)

    @ttl_cached(maxsize=128, ttl=3600)
    async def get_tv_show_details(self, tmdb_id: int) -> dict[str, Any]:
        return await super().get_tv_show_details(tmdb_id)

    @ttl_cached(maxsize=128, ttl=3600)
    async def get_season_episodes(
        self, tmdb_id: int, season_number: int
    ) -> list[dict[str, Any]]:
        return await super().get_season_episodes(tmdb_id, season_number)


_cached_client: CachedTMDBClient | None = None


def get_tmdb_client() -> CachedTMDBClient:
    global _cached_client
    if _cached_client is None:
        _cached_client = CachedTMDBClient()
    return _cached_client


@atexit.register
def _cleanup_tmdb_client_session() -> None:
    """Best-effort cleanup of the shared aiohttp session at interpreter exit."""
    client = _cached_client
    if client is None:
        return

    try:
        asyncio.run(client.close())
    except RuntimeError:
        # A loop is already running or other edge-case at shutdown; skip.
        pass
