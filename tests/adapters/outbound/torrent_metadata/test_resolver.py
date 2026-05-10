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
            "[Rare] 足球小将系列.1983-2018.Captain Tsubasa BATCH JP DVDRemux(比较全面）",
            "Anime 01-24 [1080p]",
            "Show S01E01-E12",
            "Anime BD BOX",
            "Show 总集篇 SP",
            "【SW字幕组】[宠物小精灵 / 宝可梦 地平线 再度飞升][123-132][简日双语字幕][1080P][AVC][MP4][CHS_JP]",
            "[天月搬运组][星球大战：异等小队/Star Wars: The Bad Batch 第一季][全16集][英语中字][1080P][Disney+]",
            "[VCB-Studio] 青春之旅 / Ao Haru Ride / アオハライド 10-bit 1080p HEVC BDRip [TV + OAD Fin]",
            "Black Butler Season 1-3 + OVA 1-6 (Sub) (1920x1080) [Phr0stY] [AnimeRG] <黑执事>",
            "机械女神 Saber Marionette 银河醒目女 机械女神J+机械女神J to X+机械女神J again+机械女神R 全套DVDRIP 日英双音轨+外挂中文内挂英文字幕",
            "娜娜/奈奈/世界上的另一个我 NANA -ナナ- 1-47 [DVDRip 1280x720 x264 FLAC]（2006年）重新打包",
            "[Kamihikouki-Rip] 人鱼之森 人鱼の森 Ningyo no Mori 1-13+OVA (DVDRIP 1280x720 x264 AC3)（2003年）重新打包",
            "[Ohys-Raws] 其中1个是妹妹!／三人行必有我妹 [BD 1280x720 x264 AAC] - 百度网盘打包下载",
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
            "[黒ネズミたち] 宗门里除了我都是卧底 / Spy x Sect - 123 (B-Global Donghua 1920x1080 HEVC AAC MKV)",
            "【枫叶字幕组】[宠物小精灵 / 宝可梦 地平线 再度飞升][123][简体][1080P][MP4]",
            "[Group] Star Wars: The Bad Batch - 01 [1080P][CHS]",
            "Show S02 - 14 [1080p]",
            "[梦蓝字幕组]CrayonshinChan 蜡笔小新[2018.11.16][982][帮忙打包&爷爷来了哦][HDTV][简日][MP4]",
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
