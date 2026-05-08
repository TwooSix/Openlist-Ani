"""
Mikan (mikanani.me) integration module.

Provides an async client for authenticating with Mikan and
subscribing to anime (bangumi) on the platform.
"""

from .client import MikanClient

__all__ = [
    "MikanClient",
]
