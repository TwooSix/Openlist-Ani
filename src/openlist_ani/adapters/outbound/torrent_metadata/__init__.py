"""Magnet-link resolution helpers (libtorrent-backed)."""

from .resolver import (
    CollectionDetector,
    LibtorrentMetadataClient,
    MagnetResolver,
    ResolveResult,
    TorrentFile,
    TorrentFileResolver,
    detect_collection,
    resolve_magnet,
    resolve_torrent,
)

__all__ = [
    "CollectionDetector",
    "LibtorrentMetadataClient",
    "MagnetResolver",
    "ResolveResult",
    "TorrentFile",
    "TorrentFileResolver",
    "detect_collection",
    "resolve_magnet",
    "resolve_torrent",
]
