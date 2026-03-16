"""Decorator for caching async instance method results in a ``TTLCache``.

The cache is auto-created on first call — no manual setup in ``__init__``
is required.

Usage::

    class MyClient:
        @ttl_cached(maxsize=256, ttl=3600)
        async def fetch(self, item_id: int) -> Item:
            return await self._request(f"/items/{item_id}")

        @ttl_cached(maxsize=256, ttl=3600, key=lambda q: q.strip().lower())
        async def search(self, query: str) -> list[dict]:
            return await self._api_search(query)

        @ttl_cached(maxsize=1, ttl=600)
        async def fetch_collections(self) -> list[Entry]:
            ...

        async def modify_collection(self):
            ...
            # Type-safe cache invalidation via method reference:
            clear_cache(self.fetch_collections)
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Hashable
from typing import Any

from cachetools import TTLCache

_MISSING = object()


def ttl_cached(
    *,
    maxsize: int = 256,
    ttl: int = 3600,
    key: Callable[..., Hashable] | None = None,
    name: str | None = None,
) -> Callable:
    """Cache an async method's return value in an auto-managed ``TTLCache``.

    Falsy return values (``None``, ``[]``, ``{}``, …) are never cached.

    Args:
        maxsize: Maximum cache entries.
        ttl: Time-to-live in seconds.
        key: Optional callable that receives the same ``*args, **kwargs`` as
            the decorated method (**excluding** ``self``) and returns a
            hashable cache key.  When omitted, the key is derived
            automatically: single-arg → that arg, multi-arg → tuple of
            all args, no-arg → ``()``.
        name: Attribute name for the cache on ``self``.  Defaults to
            ``_cache_{method_name}``.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        cache_attr = name or f"_cache_{func.__name__}"

        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            cache = getattr(self, cache_attr, None)
            if cache is None:
                cache = TTLCache(maxsize=maxsize, ttl=ttl)
                setattr(self, cache_attr, cache)

            cache_key = _make_cache_key(key, args, kwargs)

            cached = cache.get(cache_key, _MISSING)
            if cached is not _MISSING:
                return cached

            result = await func(self, *args, **kwargs)
            if result:
                cache[cache_key] = result
            return result

        wrapper._cache_attr = cache_attr  # type: ignore[attr-defined]
        return wrapper

    return decorator


def clear_cache(bound_method: Any) -> None:
    """Clear the TTL cache for a ``@ttl_cached`` bound method.

    Type-safe alternative to accessing the cache attribute by name.

    Usage::

        clear_cache(self.fetch_user_collections)

    Args:
        bound_method: A bound method decorated with ``@ttl_cached``.

    Raises:
        TypeError: If *bound_method* is not a ``@ttl_cached`` bound method.
    """
    fn = getattr(bound_method, "__func__", None)
    attr = getattr(fn, "_cache_attr", None) if fn else None
    if attr is None:
        raise TypeError(
            f"{bound_method!r} is not a bound method decorated with @ttl_cached"
        )
    instance = bound_method.__self__
    cache = getattr(instance, attr, None)
    if cache is not None:
        cache.clear()


def _make_cache_key(
    key_func: Callable[..., Hashable] | None,
    args: tuple,
    kwargs: dict[str, Any],
) -> Hashable:
    """Build a hashable cache key from the method arguments."""
    if key_func is not None:
        return key_func(*args, **kwargs)
    if args:
        return args[0] if len(args) == 1 else args
    return ()
