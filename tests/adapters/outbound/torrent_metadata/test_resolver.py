"""Tests for the magnet resolver: dn= parsing + collection detection."""

from __future__ import annotations

import pytest

from openlist_ani.adapters.outbound.torrent_metadata import resolver as r


class TestDetectCollection:
    @pytest.mark.parametrize(
        "title",
        [
            "[Sakurato] Anime Title 合集 [BDRip]",
            "[字幕组] 番剧 全集",
            "[Group] Show Complete BDRip",
            "[Foo] Show Batch 01-12",
            "Anime 01-24 [1080p]",
            "Show S01E01-E12",
            "Anime 01~24 [BDRip]",
            "Show Season 1 Complete",
            "Anime BD BOX",
            "Show 总集篇 SP",
        ],
    )
    def test_positive(self, title):
        is_coll, reason = r.detect_collection(title)
        assert is_coll is True, title
        assert reason

    @pytest.mark.parametrize(
        "title",
        [
            "[Sakurato] Anime Title - 01 [1080p]",
            "[字幕组] 番剧 - 12 [简体][1080p]",
            "Show S01E05 1080p",
            "Anime - 03v2 [WebRip]",
        ],
    )
    def test_negative(self, title):
        is_coll, _ = r.detect_collection(title)
        assert is_coll is False, title


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
        assert out.is_collection is False

    @pytest.mark.asyncio
    async def test_dn_path_collection_flagged(self, monkeypatch):
        monkeypatch.setattr(r, "_fetch_metadata_blocking", lambda *a, **kw: (None, []))
        m = (
            "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
            "&dn=Show%20Complete%20BDRip"
        )
        out = await r.resolve_magnet(m, metadata_timeout=5)
        assert out.success is True
        assert out.is_collection is True
        assert out.collection_reason

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

    @pytest.mark.asyncio
    async def test_metadata_timeout_no_dn(self, monkeypatch):
        monkeypatch.setattr(r, "_fetch_metadata_blocking", lambda *a, **kw: (None, []))
        m = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        out = await r.resolve_magnet(m, metadata_timeout=2)
        assert out.success is False
        assert out.title is None
        assert out.message
