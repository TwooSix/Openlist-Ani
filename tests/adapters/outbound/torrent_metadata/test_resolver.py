"""Tests for the magnet resolver: dn= and metadata title extraction."""

from __future__ import annotations

import asyncio

import pytest

from openlist_ani.adapters.outbound.torrent_metadata import resolver as r


class TestResolveMagnet:
    @pytest.mark.asyncio
    async def test_invalid_magnet(self):
        out = await r.resolve_magnet("not a magnet")
        assert out.success is False
        assert out.title is None

    @pytest.mark.asyncio
    async def test_dn_path_short_circuit(self, monkeypatch):
        monkeypatch.setattr(r, "_fetch_metadata_blocking", lambda *a, **kw: (None, []))
        m = (
            "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
            "&dn=My%20Show%20-%2001%20%5B1080p%5D"
        )
        out = await r.resolve_magnet(m, metadata_timeout=5)
        assert out.success is True
        assert out.title == "My Show - 01 [1080p]"
        assert out.source == "dn"
        assert not hasattr(out, "is_collection")
        assert not hasattr(out, "collection_reason")

    async def test_dn_path_collection_title_is_not_classified(self, monkeypatch):
        monkeypatch.setattr(r, "_fetch_metadata_blocking", lambda *a, **kw: (None, []))
        m = (
            "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
            "&dn=Show%20Complete%20BDRip"
        )
        out = await r.resolve_magnet(m, metadata_timeout=5)
        assert out.success is True
        assert out.title == "Show Complete BDRip"
        assert not hasattr(out, "is_collection")
        assert not hasattr(out, "collection_reason")

    @pytest.mark.asyncio
    async def test_metadata_fallback_success(self, monkeypatch):
        def _fake(magnet, timeout):
            return "Show - 02 [WebRip]", [
                r.TorrentFile(name="Show - 02.mkv", size=123456789)
            ]

        monkeypatch.setattr(r, "_fetch_metadata_blocking", _fake)
        m = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        out = await r.resolve_magnet(m, metadata_timeout=5)
        assert out.success is True
        assert out.title == "Show - 02 [WebRip]"
        assert out.source == "metadata"
        assert out.file_count == 1
        assert not hasattr(out, "is_collection")
        assert not hasattr(out, "collection_reason")

    @pytest.mark.asyncio
    async def test_metadata_timeout_no_dn(self, monkeypatch):
        monkeypatch.setattr(r, "_fetch_metadata_blocking", lambda *a, **kw: (None, []))
        m = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        out = await r.resolve_magnet(m, metadata_timeout=2)
        assert out.success is False
        assert out.title is None
        assert out.message


class FakeTorrentMetadataClient:
    async def parse_torrent_blob(self, blob):
        await asyncio.sleep(0)
        return "Show Complete BDRip", [r.TorrentFile(name="Show Complete.mkv")]


class TestResolveTorrent:
    @pytest.mark.asyncio
    async def test_torrent_file_title_is_not_classified(self, monkeypatch):
        async def fake_download(url):
            await asyncio.sleep(0)
            return b"torrent-bytes", None

        monkeypatch.setattr(r, "_download_torrent_bytes", fake_download)
        resolver = r.TorrentFileResolver(metadata_client=FakeTorrentMetadataClient())

        out = await resolver.resolve("https://example.invalid/show.torrent")

        assert out.success is True
        assert out.title == "Show Complete BDRip"
        assert out.source == "torrent_file"
        assert not hasattr(out, "is_collection")
        assert not hasattr(out, "collection_reason")
