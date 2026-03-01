"""
Async HTTP client for the Bangumi API.

Provides methods for fetching calendar, subject details, and user collections
with TTL caching and proper rate limiting.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from cachetools import TTLCache

from ...logger import logger
from .model import (
    BangumiBlog,
    BangumiSubject,
    BangumiTopic,
    BangumiUser,
    CalendarDay,
    RelatedSubject,
    UserCollectionEntry,
    parse_calendar_day,
    parse_legacy_blog,
    parse_legacy_topic,
    parse_related_subject,
    parse_subject,
    parse_user,
    parse_user_collection_entry,
)

_API_BASE_URL = "https://api.bgm.tv"
_USER_AGENT = "openlist-ani/1.0 (https://github.com/Openlist-Ani)"
_REQUEST_INTERVAL = 0.5  # seconds between requests to avoid rate limiting
_DEFAULT_PAGE_LIMIT = 50  # max allowed by API
_DEFAULT_EP_PAGE_LIMIT = 100


class BangumiClient:
    """Async client for Bangumi API with TTL caching.

    Args:
        access_token: Bangumi API access token for authenticated requests.
    """

    def __init__(self, access_token: str = "") -> None:
        self._access_token = access_token
        self._session: aiohttp.ClientSession | None = None
        self._last_request_time: float = 0.0

        # Caches
        self._user_cache: BangumiUser | None = None
        self._calendar_cache: TTLCache[str, list[CalendarDay]] = TTLCache(
            maxsize=1, ttl=6 * 3600
        )
        self._subject_cache: TTLCache[int, BangumiSubject] = TTLCache(
            maxsize=256, ttl=3600
        )
        self._collection_cache: TTLCache[str, list[UserCollectionEntry]] = TTLCache(
            maxsize=1, ttl=600
        )
        self._related_cache: TTLCache[int, list[RelatedSubject]] = TTLCache(
            maxsize=64, ttl=3600
        )

    def _ensure_session(self) -> aiohttp.ClientSession:
        """Create or return the shared aiohttp session."""
        if self._session is None or self._session.closed:
            headers: dict[str, str] = {
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            }
            if self._access_token:
                headers["Authorization"] = f"Bearer {self._access_token}"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _throttle(self) -> None:
        """Ensure minimum interval between consecutive API requests."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < _REQUEST_INTERVAL:
            await asyncio.sleep(_REQUEST_INTERVAL - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Send an HTTP request to the Bangumi API.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.).
            path: API path (e.g. "/calendar").
            params: Optional query parameters.
            json_body: Optional JSON request body for POST/PATCH.

        Returns:
            Parsed JSON response, or None for 204 No Content.

        Raises:
            aiohttp.ClientResponseError: On HTTP error responses.
        """
        session = self._ensure_session()
        await self._throttle()

        url = f"{_API_BASE_URL}{path}"
        logger.debug(f"Bangumi API request: {method} {url} params={params}")

        async with session.request(method, url, params=params, json=json_body) as resp:
            if resp.status == 204:
                return None
            if resp.status == 401:
                logger.error("Bangumi API: Unauthorized (invalid or missing token)")
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=401,
                    message="Unauthorized – check your Bangumi access token",
                )
            if resp.status == 404:
                logger.warning(f"Bangumi API: Not found – {url}")
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=404,
                    message="Not found",
                )
            if resp.status == 429:
                logger.warning("Bangumi API: Rate limited (429)")
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=429,
                    message="Rate limited – slow down requests",
                )
            resp.raise_for_status()

            # Some successful endpoints (e.g. collection update POST) may
            # return 202 Accepted with an empty or non-JSON body.
            raw = await resp.read()
            if not raw:
                return None

            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.debug(
                    f"Bangumi API: Non-JSON success response "
                    f"(status={resp.status}) for {method} {path}"
                )
                return None

    # ---- Public API methods ----

    async def fetch_current_user(self) -> BangumiUser:
        """Fetch the current authenticated user info (GET /v0/me).

        Caches the result permanently for the lifetime of this client.

        Returns:
            BangumiUser for the current token.

        Raises:
            aiohttp.ClientResponseError: On auth failure.
        """
        if self._user_cache is not None:
            return self._user_cache

        data = await self._request("GET", "/v0/me")
        self._user_cache = parse_user(data)
        logger.info(
            f"Bangumi: Authenticated as {self._user_cache.nickname} "
            f"(@{self._user_cache.username})"
        )
        return self._user_cache

    async def fetch_calendar(self) -> list[CalendarDay]:
        """Fetch the weekly anime airing calendar (GET /calendar).

        Cached for 6 hours.

        Returns:
            List of CalendarDay, one per day of the week.
        """
        cache_key = "calendar"
        if cache_key in self._calendar_cache:
            logger.debug("Bangumi: Calendar cache hit")
            return self._calendar_cache[cache_key]

        data = await self._request("GET", "/calendar")
        days = [parse_calendar_day(d) for d in data]
        self._calendar_cache[cache_key] = days
        logger.info(
            f"Bangumi: Fetched calendar with {sum(len(d.items) for d in days)} titles"
        )
        return days

    async def fetch_subject(self, subject_id: int) -> BangumiSubject:
        """Fetch full subject details (GET /v0/subjects/{id}).

        Cached for 1 hour per subject.

        Args:
            subject_id: Bangumi subject ID.

        Returns:
            BangumiSubject with full details.
        """
        if subject_id in self._subject_cache:
            logger.debug(f"Bangumi: Subject {subject_id} cache hit")
            return self._subject_cache[subject_id]

        data = await self._request("GET", f"/v0/subjects/{subject_id}")
        subject = parse_subject(data)
        self._subject_cache[subject_id] = subject
        logger.debug(f"Bangumi: Fetched subject {subject_id} – {subject.display_name}")
        return subject

    async def fetch_user_collections(
        self,
        subject_type: int | None = 2,
        collection_type: int | None = None,
    ) -> list[UserCollectionEntry]:
        """Fetch the current user's anime collection list.

        Automatically fetches the current username via /v0/me.
        Handles pagination to retrieve all results.
        Cached for 10 minutes.

        Args:
            subject_type: SubjectType filter (default 2 = anime). None for all.
            collection_type: CollectionType filter. None for all.

        Returns:
            List of UserCollectionEntry objects.
        """
        cache_key = f"{subject_type}_{collection_type}"
        if cache_key in self._collection_cache:
            logger.debug("Bangumi: User collection cache hit")
            return self._collection_cache[cache_key]

        user = await self.fetch_current_user()
        username = user.username

        all_entries: list[UserCollectionEntry] = []
        offset = 0
        while True:
            params: dict[str, Any] = {
                "limit": _DEFAULT_PAGE_LIMIT,
                "offset": offset,
            }
            if subject_type is not None:
                params["subject_type"] = subject_type
            if collection_type is not None:
                params["type"] = collection_type

            data = await self._request(
                "GET", f"/v0/users/{username}/collections", params=params
            )

            entries = [
                parse_user_collection_entry(item) for item in data.get("data", [])
            ]
            all_entries.extend(entries)

            total = data.get("total", 0)
            offset += _DEFAULT_PAGE_LIMIT
            if offset >= total:
                break

        self._collection_cache[cache_key] = all_entries
        logger.info(
            f"Bangumi: Fetched {len(all_entries)} collection entries "
            f"for @{username}"
        )
        return all_entries

    async def fetch_related_subjects(self, subject_id: int) -> list[RelatedSubject]:
        """Fetch subjects related to the given subject.

        Returns sequel, prequel, side-story and other relations.
        Cached for 1 hour per subject.

        Args:
            subject_id: Bangumi subject ID.

        Returns:
            List of RelatedSubject with relation type and slim subject info.
        """
        if subject_id in self._related_cache:
            logger.debug(f"Bangumi: Related subjects {subject_id} cache hit")
            return self._related_cache[subject_id]

        data = await self._request("GET", f"/v0/subjects/{subject_id}/subjects")
        items = [parse_related_subject(item) for item in (data or [])]
        self._related_cache[subject_id] = items
        logger.debug(
            f"Bangumi: Fetched {len(items)} related subjects "
            f"for subject {subject_id}"
        )
        return items

    async def fetch_subject_reviews(
        self, subject_id: int
    ) -> tuple[list[BangumiTopic], list[BangumiBlog]]:
        """Fetch discussion topics and blog reviews for a subject.

        Uses the legacy API endpoint (responseGroup=large) which includes
        topic and blog data not available in the v0 API.

        Args:
            subject_id: Bangumi subject ID.

        Returns:
            Tuple of (topics list, blogs list).
        """
        data = await self._request(
            "GET",
            f"/subject/{subject_id}",
            params={"responseGroup": "large"},
        )

        topics = [parse_legacy_topic(t) for t in (data.get("topic") or [])]
        blogs = [parse_legacy_blog(b) for b in (data.get("blog") or [])]
        logger.info(
            f"Bangumi: Fetched {len(topics)} topics, {len(blogs)} blogs "
            f"for subject {subject_id}"
        )
        return topics, blogs

    async def post_user_collection(
        self,
        subject_id: int,
        collection_type: int | None = None,
        rate: int | None = None,
        comment: str | None = None,
        tags: list[str] | None = None,
        private: bool | None = None,
        ep_status: int | None = None,
    ) -> None:
        """Create or modify a collection entry for the current user.

        Uses POST ``/v0/users/-/collections/{subject_id}`` to create /
        set the collection type, then PATCH the same endpoint when
        ``ep_status`` is provided (the POST endpoint does **not** accept
        ``ep_status``).

        Args:
            subject_id: Bangumi subject ID.
            collection_type: Collection type (1=wish, 2=done, 3=doing,
                4=on_hold, 5=dropped).
            rate: Rating 0-10 (0 to remove rating).
            comment: User comment/review text.
            tags: List of tags.
            private: Whether the collection is private.
            ep_status: Number of watched episodes.

        Raises:
            aiohttp.ClientResponseError: On API errors.
        """
        path = f"/v0/users/-/collections/{subject_id}"

        # --- Step 1: POST to create / update the collection entry ----
        post_body: dict[str, Any] = {}
        if collection_type is not None:
            post_body["type"] = collection_type
        if rate is not None:
            post_body["rate"] = rate
        if comment is not None:
            post_body["comment"] = comment
        if tags is not None:
            post_body["tags"] = tags
        if private is not None:
            post_body["private"] = private

        if post_body:
            await self._request("POST", path, json_body=post_body)

        # --- Step 2: PATCH to set ep_status (not supported by POST) --
        if ep_status is not None:
            await self._request("PATCH", path, json_body={"ep_status": ep_status})

        # Invalidate collection cache since we modified it
        self._collection_cache.clear()

        logger.info(
            f"Bangumi: Updated collection for subject {subject_id} "
            f"(type={collection_type}, ep_status={ep_status})"
        )

    async def fetch_subject_episodes(
        self,
        subject_id: int,
        episode_type: int | None = 0,
    ) -> list[dict[str, Any]]:
        """Fetch all episodes for a subject.

        Uses ``GET /v0/episodes`` with pagination.

        Args:
            subject_id: Bangumi subject ID.
            episode_type: Episode type filter (default 0 = main story).
                Use None to fetch all types.

        Returns:
            List of raw episode dicts from API.
        """
        all_episodes: list[dict[str, Any]] = []
        offset = 0

        while True:
            params: dict[str, Any] = {
                "subject_id": subject_id,
                "limit": _DEFAULT_EP_PAGE_LIMIT,
                "offset": offset,
            }
            if episode_type is not None:
                params["type"] = episode_type

            data = await self._request("GET", "/v0/episodes", params=params)
            items = (data or {}).get("data", [])
            if not items:
                break

            all_episodes.extend(items)
            total = (data or {}).get("total", 0)
            offset += _DEFAULT_EP_PAGE_LIMIT
            if offset >= total:
                break

        logger.debug(
            f"Bangumi: Fetched {len(all_episodes)} episodes "
            f"for subject {subject_id}"
        )
        return all_episodes

    async def patch_subject_episode_collections(
        self,
        subject_id: int,
        episode_ids: list[int],
        collection_type: int = 2,
    ) -> None:
        """Batch update episode collection status for a subject.

        Uses ``PATCH /v0/users/-/collections/{subject_id}/episodes``.

        Args:
            subject_id: Bangumi subject ID.
            episode_ids: Episode ID list to update.
            collection_type: Episode collection type
                (0=remove, 1=wish, 2=done, 3=dropped).
        """
        if not episode_ids:
            return

        payload = {
            "episode_id": episode_ids,
            "type": collection_type,
        }
        await self._request(
            "PATCH",
            f"/v0/users/-/collections/{subject_id}/episodes",
            json_body=payload,
        )

        # Episode updates also affect visible progress; invalidate cache.
        self._collection_cache.clear()
        logger.info(
            f"Bangumi: Updated {len(episode_ids)} episodes for subject "
            f"{subject_id} (type={collection_type})"
        )
