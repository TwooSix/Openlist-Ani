import pytest

from openlist_ani.domain.anime_release import detect_collection


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
            "【SW字幕组】[宠物小精灵 / 宝可梦 地平线 再度飞升][123-132][简日双语字幕][1080P][AVC][MP4][CHS_JP]",
            "[天月搬运组][星球大战：异等小队/Star Wars: The Bad Batch 第一季][全16集][英语中字][1080P][Disney+]",
            "[VCB-Studio] 青春之旅 / Ao Haru Ride / アオハライド 10-bit 1080p HEVC BDRip [TV + OAD Fin]",
            "Black Butler Season 1-3 + OVA 1-6 (Sub) (1920x1080) [Phr0stY] [AnimeRG] <黑执事>",
            "机械女神 Saber Marionette 银河醒目女 机械女神J+机械女神J to X+机械女神J again+机械女神R 全套DVDRIP 日英双音轨+外挂中文内挂英文字幕",
            "娜娜/奈奈/世界上的另一个我 NANA -ナナ- 1-47 [DVDRip 1280x720 x264 FLAC]（2006年）重新打包",
            "[Kamihikouki-Rip] 人鱼之森 人鱼の森 Ningyo no Mori 1-13+OVA (DVDRIP 1280x720 x264 AC3)（2003年）重新打包",
            "[Ohys-Raws] 其中1个是妹妹!／三人行必有我妹 [BD 1280x720 x264 AAC] - 百度网盘打包下载",
            "29岁单身中坚冒险家的日常 - EP01 ~ EP10 [简／繁] (1080p H.264 AAC SRTx2) {An Adventurer's Daily Grind at Age 29 | 29歳独身中坚冒険者の日常}",
            "异世界四重奏3 - EP01~EP06 [简／繁] (1080p H.264 AAC2.0+DDP2.0 SRTx2) {异世界四重奏 | 异世界かるてっと}",
            "【喵萌奶茶屋】★10月新番★[弹珠汽水瓶里的千岁同学 / 千歳くんはラムネ瓶のなか / Chitose-kun wa Ramune Bin no Naka][07+ES07][WebRip 1080p HEVC-10bit AAC][简繁日内封]",
            '[芝士动物朋友] 「凭你也想讨伐魔王？」被勇者小队逐出队伍，只好在王都自在过活 / "Omae Gotoki ga Maou ni Kateru to Omouna" to Yuusha Party wo Tsuihou sareta node, Outo de Kimama ni Kurashitai / 「お前ごときが魔王に胜てると思うな」と勇者パーティを追放されたので、王都で気ままに暮らしたい / Omagoto [1-12][CR-WebRip 1080p HEVC AAC][简繁内封]【CR官译】',
            "[芝士动物朋友] 转生之后的我变成了龙蛋 / Tensei Shitara Dragon no Tamago Datta / 転生したらドラゴンの卵だった [1-12][AMZN-WebRemux 1080p AVC EAC3 SRT][简繁内封]【CatchPlay官译】",
        ],
    )
    def test_positive(self, title):
        is_coll, reason = detect_collection(title)
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
            "[黒ネズミたち] Dr.STONE 新石纪 第四季 / Dr. Stone: Science Future Part 3 - 30 (Baha 1920x1080 AVC AAC MP4)",
            "[LoliHouse] 异世界悠闲农家 2 / Isekai Nonbiri Nouka 2 - 05 [WebRip 1080p HEVC-10bit AAC][简繁内封字幕]",
            "[黒ネズミたち] 链锯人 总集篇 / Chainsaw Man Recap - 02 (Baha 1920x1080 AVC AAC MP4)",
            "[ANi] Tsue to Tsurugi no Wistoria /  杖与剑的魔剑谭 Season 2 - 18 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
            "[梦蓝字幕组]CrayonshinChan 蜡笔小新[2018.11.16][982][帮忙打包&爷爷来了哦][HDTV][简日][MP4]",
        ],
    )
    def test_negative(self, title):
        is_coll, _ = detect_collection(title)
        assert is_coll is False, title
