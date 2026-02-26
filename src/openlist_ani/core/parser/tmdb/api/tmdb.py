import asyncio
import atexit
from typing import Any, Dict, List, Optional

import aiohttp
from cachetools import TTLCache

from openlist_ani.config import config
from openlist_ani.logger import logger


class TMDBClient:
    def __init__(self):
        self.base_url = "https://api.tmdb.org/3"
        self._timeout = aiohttp.ClientTimeout(total=30, connect=30, sock_read=30)
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def api_key(self) -> str:
        return config.llm.tmdb_api_key

    async def search_tv_show(self, query: str) -> List[Dict[str, Any]]:
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

    async def get_tv_show_details(self, tmdb_id: int) -> Dict[str, Any]:
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
    ) -> List[Dict[str, Any]]:
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

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, trust_env=True)
        return self._session

    async def _request_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = await self._get_session()
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class CachedTMDBClient(TMDBClient):

    def __init__(
        self,
        search_maxsize: int = 256,
        details_maxsize: int = 128,
        ttl: int = 3600,
    ):
        super().__init__()
        self._search_cache: TTLCache = TTLCache(maxsize=search_maxsize, ttl=ttl)
        self._details_cache: TTLCache = TTLCache(maxsize=details_maxsize, ttl=ttl)
        self._season_eps_cache: TTLCache = TTLCache(maxsize=details_maxsize, ttl=ttl)

    async def search_tv_show(self, query: str) -> List[Dict[str, Any]]:
        cache_key = query.strip().lower()
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"TMDB search cache hit: {query}")
            return cached
        result = await super().search_tv_show(query)
        if result:
            self._search_cache[cache_key] = result
        return result

    async def get_tv_show_details(self, tmdb_id: int) -> Dict[str, Any]:
        cached = self._details_cache.get(tmdb_id)
        if cached is not None:
            logger.debug(f"TMDB details cache hit: {tmdb_id}")
            return cached
        result = await super().get_tv_show_details(tmdb_id)
        if result:
            self._details_cache[tmdb_id] = result
        return result

    async def get_season_episodes(
        self, tmdb_id: int, season_number: int
    ) -> List[Dict[str, Any]]:
        cache_key = (tmdb_id, season_number)
        cached = self._season_eps_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"TMDB season episodes cache hit: {tmdb_id} S{season_number}")
            return cached
        result = await super().get_season_episodes(tmdb_id, season_number)
        if result:
            self._season_eps_cache[cache_key] = result
        return result


_cached_client: Optional[CachedTMDBClient] = None


def get_tmdb_client() -> CachedTMDBClient:
    global _cached_client
    if _cached_client is None:
        _cached_client = CachedTMDBClient()
    return _cached_client


@atexit.register
def _cleanup_tmdb_client_session() -> None:
    client = _cached_client
    if client is None:
        return

    try:
        asyncio.run(client.close())
    except Exception:
        pass
