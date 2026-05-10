from __future__ import annotations

import asyncio

import pytest

from openlist_ani.adapters.outbound.notifications.bot.wechat import WechatBot
from openlist_ani.integrations.messaging.wechat_ilink import WechatIlinkMessenger


class FakeWechatMessenger:
    platform = "wechat"

    def __init__(self) -> None:
        self.sent: list[tuple[str | None, str]] = []
        self.auth_checked = 0

    async def ensure_auth(self) -> dict[str, str]:
        await asyncio.sleep(0)
        self.auth_checked += 1
        return {
            "account_id": "bot@im.bot",
            "token": "token",
            "base_url": "https://wx",
        }

    async def send_text(self, chat_id: str | None, text: str) -> bool:
        await asyncio.sleep(0)
        self.sent.append((chat_id, text))
        return True


@pytest.mark.asyncio
async def test_wechat_bot_start_only_checks_auth():
    messenger = FakeWechatMessenger()
    bot = WechatBot(chat_id="configured@im.wechat", messenger=messenger)

    await bot.start()

    assert messenger.sent == []
    assert messenger.auth_checked == 1


@pytest.mark.asyncio
async def test_wechat_bot_start_requires_setup_when_auth_is_missing():
    bot = WechatBot(messenger=WechatIlinkMessenger(interactive_login=False))

    with pytest.raises(RuntimeError, match="openlist-ani-wechat-login"):
        await bot.start()
