"""Magnet-link resolution helpers (libtorrent-backed)."""

from .resolver import (
    LibtorrentMetadataClient,
    MagnetResolver,
    ResolveResult,
    TorrentFile,
    TorrentFileResolver,
    resolve_magnet,
    resolve_torrent,
)

__all__ = [
    "LibtorrentMetadataClient",
    "MagnetResolver",
    "ResolveResult",
    "TorrentFile",
    "TorrentFileResolver",
    "resolve_magnet",
    "resolve_torrent",
]
