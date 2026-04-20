"""Magnet-link resolution helpers (libtorrent-backed)."""

from .resolver import (
    ResolveResult,
    TorrentFile,
    detect_collection,
    resolve_magnet,
    resolve_torrent,
)

__all__ = [
    "ResolveResult",
    "TorrentFile",
    "detect_collection",
    "resolve_magnet",
    "resolve_torrent",
]
