"""Small async TTL cache helper for assistant skill support clients."""

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
    """Cache an async method's truthy result in an instance TTLCache."""

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
    """Clear the cache created by ``ttl_cached`` for a bound method."""
    fn = getattr(bound_method, "__func__", None)
    attr = getattr(fn, "_cache_attr", None) if fn else None
    if attr is None:
        raise TypeError(
            f"{bound_method!r} is not a bound method decorated with @ttl_cached"
        )
    cache = getattr(bound_method.__self__, attr, None)
    if cache is not None:
        cache.clear()


def _make_cache_key(
    key_func: Callable[..., Hashable] | None,
    args: tuple,
    kwargs: dict[str, Any],
) -> Hashable:
    if key_func is not None:
        return key_func(*args, **kwargs)
    sorted_kwargs = tuple(sorted(kwargs.items())) if kwargs else ()
    if args and sorted_kwargs:
        return args + sorted_kwargs
    if args:
        return args[0] if len(args) == 1 else args
    if sorted_kwargs:
        return sorted_kwargs
    return ()
