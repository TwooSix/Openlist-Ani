"""
Backward-compatibility shim.

Workers have been moved to ``openlist_ani.backend.worker``.
This module re-exports them so existing imports continue to work.
"""

from .backend.worker import (
    dispatch_downloads,
    poll_rss_feeds,
)

__all__ = ["dispatch_downloads", "poll_rss_feeds"]
