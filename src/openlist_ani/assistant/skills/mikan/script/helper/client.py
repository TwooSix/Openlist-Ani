"""Mikan client singleton management."""

from __future__ import annotations

from openlist_ani.config import config
from openlist_ani.core.mikan.client import MikanClient

# Shared client instance (lazy init)
_mikan_client: MikanClient | None = None


def _get_mikan_client() -> MikanClient | None:
    """Get or create the shared MikanClient singleton.

    Returns:
        MikanClient instance, or None if credentials are not configured.
    """
    global _mikan_client
    username = config.mikan.username
    password = config.mikan.password
    if not username or not password:
        return None
    if _mikan_client is None:
        _mikan_client = MikanClient(username=username, password=password)
    return _mikan_client


async def close_mikan_client() -> None:
    """Close shared Mikan client session if initialized."""
    global _mikan_client
    if _mikan_client is None:
        return

    await _mikan_client.close()
    _mikan_client = None
