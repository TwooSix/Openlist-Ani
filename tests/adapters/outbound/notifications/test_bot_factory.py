from __future__ import annotations

import pytest

from openlist_ani.adapters.outbound.notifications.bot.factory import BotFactory
from openlist_ani.adapters.outbound.notifications.bot.feishu import FeishuBot
from openlist_ani.adapters.outbound.notifications.bot.wechat import WechatBot


def test_factory_creates_wechat_bot():
    bot = BotFactory().create_bot(
        "wechat",
        {
            "account_id": "bot@im.bot",
            "token": "token",
            "home_channel": "user@im.wechat",
        },
    )

    assert isinstance(bot, WechatBot)


def test_factory_rejects_wechat_without_setup_output():
    with pytest.raises(ValueError, match="account_id"):
        BotFactory().create_bot("wechat", {})


def test_factory_creates_feishu_bot_with_app_credentials():
    bot = BotFactory().create_bot(
        "feishu",
        {
            "app_id": "cli_xxx",
            "app_secret": "secret",
        },
    )

    assert isinstance(bot, FeishuBot)


def test_factory_rejects_feishu_without_app_credentials():
    with pytest.raises(ValueError, match="app_id"):
        BotFactory().create_bot("feishu", {})
