"""Tests for CachedTMDBClient."""

from unittest.mock import AsyncMock, patch

import pytest

from openlist_ani.core.parser.tmdb.api import (
    CachedTMDBClient,
    get_tmdb_client,
)
from openlist_ani.core.parser.tmdb.api import tmdb as tmdb_module


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset module-level singleton before each test."""
    tmdb_module._cached_client = None
    yield
    tmdb_module._cached_client = None


class TestCachedTMDBClient:

    async def test_search_cache_hit(self):
        client = CachedTMDBClient()
        fake_results = [{"id": 1, "name": "Frieren"}]

        with patch.object(
            CachedTMDBClient.__bases__[0],
            "search_tv_show",
            new_callable=AsyncMock,
            return_value=fake_results,
        ) as mock_search:
            first = await client.search_tv_show("Frieren")
            second = await client.search_tv_show("Frieren")

        assert first == fake_results
        assert second == fake_results
        mock_search.assert_awaited_once()

    async def test_search_cache_key_case_insensitive(self):
        client = CachedTMDBClient()
        fake_results = [{"id": 1, "name": "Frieren"}]

        with patch.object(
            CachedTMDBClient.__bases__[0],
            "search_tv_show",
            new_callable=AsyncMock,
            return_value=fake_results,
        ) as mock_search:
            await client.search_tv_show("Frieren")
            await client.search_tv_show("  FRIEREN  ")

        mock_search.assert_awaited_once()

    async def test_search_empty_result_not_cached(self):
        client = CachedTMDBClient()

        call_count = 0

        def fake_search(query):
            nonlocal call_count
            call_count += 1
            return []

        with patch.object(
            CachedTMDBClient.__bases__[0],
            "search_tv_show",
            new_callable=AsyncMock,
            side_effect=fake_search,
        ):
            await client.search_tv_show("nonexistent")
            await client.search_tv_show("nonexistent")

        assert call_count == 2

    async def test_details_cache_hit(self):
        client = CachedTMDBClient()
        fake_details = {"id": 123, "name": "Frieren", "seasons": []}

        with patch.object(
            CachedTMDBClient.__bases__[0],
            "get_tv_show_details",
            new_callable=AsyncMock,
            return_value=fake_details,
        ) as mock_details:
            first = await client.get_tv_show_details(123)
            second = await client.get_tv_show_details(123)

        assert first == fake_details
        assert second == fake_details
        mock_details.assert_awaited_once()

    async def test_details_empty_result_not_cached(self):
        client = CachedTMDBClient()

        call_count = 0

        def fake_details(tmdb_id):
            nonlocal call_count
            call_count += 1
            return {}

        with patch.object(
            CachedTMDBClient.__bases__[0],
            "get_tv_show_details",
            new_callable=AsyncMock,
            side_effect=fake_details,
        ):
            await client.get_tv_show_details(999)
            await client.get_tv_show_details(999)

        assert call_count == 2

    async def test_different_queries_cached_separately(self):
        client = CachedTMDBClient()
        results_a = [{"id": 1, "name": "A"}]
        results_b = [{"id": 2, "name": "B"}]

        def fake_search(query):
            return results_a if "a" in query.lower() else results_b

        with patch.object(
            CachedTMDBClient.__bases__[0],
            "search_tv_show",
            new_callable=AsyncMock,
            side_effect=fake_search,
        ):
            a = await client.search_tv_show("A")
            b = await client.search_tv_show("B")

        assert a == results_a
        assert b == results_b


class TestGetTmdbClient:

    def test_returns_singleton(self):
        c1 = get_tmdb_client()
        c2 = get_tmdb_client()
        assert c1 is c2

    def test_returns_cached_tmdb_client_instance(self):
        client = get_tmdb_client()
        assert isinstance(client, CachedTMDBClient)
