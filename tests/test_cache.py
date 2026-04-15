"""Tests for openlist_ani.utils.cache — @ttl_cached decorator."""

from __future__ import annotations

import pytest

from openlist_ani.utils.cache import clear_cache, ttl_cached

# ---- Helper classes for testing ------------------------------------------


class FakeClient:
    """Minimal class that uses @ttl_cached to exercise the decorator."""

    def __init__(self) -> None:
        self.call_count: int = 0

    @ttl_cached(maxsize=64, ttl=300)
    async def fetch_by_id(self, item_id: int) -> str:
        self.call_count += 1
        return f"item-{item_id}"

    @ttl_cached(maxsize=64, ttl=300, key=lambda query: query.strip().lower())
    async def search(self, query: str) -> list[str]:
        self.call_count += 1
        return [f"result-for-{query.strip().lower()}"]

    @ttl_cached(maxsize=64, ttl=300)
    async def fetch_pair(self, a: int, b: int) -> str:
        self.call_count += 1
        return f"{a}-{b}"

    @ttl_cached(maxsize=64, ttl=300)
    async def fetch_all(self) -> list[str]:
        self.call_count += 1
        return ["all"]

    @ttl_cached(maxsize=64, ttl=300)
    async def fetch_maybe_none(self, item_id: int) -> str | None:
        self.call_count += 1
        return None  # always returns None

    @ttl_cached(maxsize=64, ttl=300)
    async def fetch_by_kwargs(
        self,
        *,
        subject_type: int = 2,
        collection_type: int | None = None,
    ) -> str:
        self.call_count += 1
        return f"sub={subject_type},col={collection_type}"

    @ttl_cached(maxsize=64, ttl=300)
    async def fetch_mixed(self, item_id: int, *, quality: str = "1080p") -> str:
        self.call_count += 1
        return f"{item_id}-{quality}"


# ---- Tests ---------------------------------------------------------------


class TestTTLCachedDecorator:
    """Exercise @ttl_cached on a fake client."""

    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self) -> None:
        client = FakeClient()
        # First call — miss, fetcher runs
        result = await client.fetch_by_id(1)
        assert result == "item-1"
        assert client.call_count == 1

        # Second call — hit, fetcher NOT called
        result2 = await client.fetch_by_id(1)
        assert result2 == "item-1"
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_different_keys_are_independent(self) -> None:
        client = FakeClient()
        await client.fetch_by_id(1)
        await client.fetch_by_id(2)
        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_key_function(self) -> None:
        client = FakeClient()
        r1 = await client.search("  Hello  ")
        r2 = await client.search("hello")
        assert r1 == r2
        assert client.call_count == 1  # normalized key → same cache entry

    @pytest.mark.asyncio
    async def test_tuple_key(self) -> None:
        client = FakeClient()
        r1 = await client.fetch_pair(1, 2)
        assert r1 == "1-2"
        r2 = await client.fetch_pair(1, 2)
        assert r2 == "1-2"
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_no_arg_method_with_constant_key(self) -> None:
        client = FakeClient()
        await client.fetch_all()
        await client.fetch_all()
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_none_not_cached(self) -> None:
        client = FakeClient()
        r1 = await client.fetch_maybe_none(1)
        assert r1 is None
        assert client.call_count == 1

        # None was NOT cached — fetcher runs again
        r2 = await client.fetch_maybe_none(1)
        assert r2 is None
        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_list_not_cached(self) -> None:
        """Empty collections are falsy and should NOT be cached."""

        class EmptyClient:
            def __init__(self) -> None:
                self.call_count = 0

            @ttl_cached(maxsize=16, ttl=300)
            async def fetch(self, item_id: int) -> list:
                self.call_count += 1
                return []

        client = EmptyClient()
        await client.fetch(1)
        await client.fetch(1)
        assert client.call_count == 2  # empty list not cached

    @pytest.mark.asyncio
    async def test_clear_cache_invalidates(self) -> None:
        """clear_cache() type-safe invalidation via bound method."""
        client = FakeClient()
        await client.fetch_by_id(1)
        assert client.call_count == 1

        clear_cache(client.fetch_by_id)

        await client.fetch_by_id(1)
        assert client.call_count == 2  # fetcher called again after clear

    @pytest.mark.asyncio
    async def test_clear_cache_before_first_call_is_safe(self) -> None:
        """clear_cache() is a no-op when cache hasn't been created yet."""
        client = FakeClient()
        clear_cache(client.fetch_by_id)  # should not raise

    @pytest.mark.asyncio
    async def test_clear_cache_rejects_non_decorated(self) -> None:
        """clear_cache() raises TypeError for non-@ttl_cached methods."""

        class Plain:
            def fetch(self) -> str:
                return "plain"

        with pytest.raises(TypeError):
            clear_cache(Plain().fetch)

    @pytest.mark.asyncio
    async def test_default_key_with_multiple_positional_args(self) -> None:
        """When no key= is given and multiple args are passed, uses args tuple."""

        class MultiArgClient:
            def __init__(self) -> None:
                self.call_count = 0

            @ttl_cached(maxsize=16, ttl=300)
            async def fetch(self, a: int, b: int) -> str:
                self.call_count += 1
                return f"{a}-{b}"

        client = MultiArgClient()
        await client.fetch(1, 2)
        await client.fetch(1, 2)
        assert client.call_count == 1

        await client.fetch(1, 3)
        assert client.call_count == 2

    @pytest.mark.asyncio
    async def test_kwargs_only_different_values_not_conflated(self) -> None:
        """Different kwargs must produce different cache keys (bug fix test).

        Previously, _make_cache_key ignored kwargs when no custom key function
        was provided, causing all kwargs-only calls to share cache key ().
        """
        client = FakeClient()
        r1 = await client.fetch_by_kwargs(subject_type=2)
        assert client.call_count == 1
        assert r1 == "sub=2,col=None"

        # Different kwargs should miss cache
        r2 = await client.fetch_by_kwargs(subject_type=2, collection_type=1)
        assert client.call_count == 2
        assert r2 == "sub=2,col=1"

        # Same kwargs should hit cache
        r3 = await client.fetch_by_kwargs(subject_type=2, collection_type=1)
        assert client.call_count == 2
        assert r3 == r2

    @pytest.mark.asyncio
    async def test_kwargs_only_same_values_cached(self) -> None:
        """Same kwargs should hit the cache."""
        client = FakeClient()
        await client.fetch_by_kwargs(subject_type=3)
        await client.fetch_by_kwargs(subject_type=3)
        assert client.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_args_and_kwargs(self) -> None:
        """Methods with both positional args and keyword args."""
        client = FakeClient()
        r1 = await client.fetch_mixed(1, quality="1080p")
        assert client.call_count == 1

        # Same call → cached
        r2 = await client.fetch_mixed(1, quality="1080p")
        assert client.call_count == 1
        assert r1 == r2

        # Different kwarg → new call
        r3 = await client.fetch_mixed(1, quality="720p")
        assert client.call_count == 2
        assert r3 != r1
