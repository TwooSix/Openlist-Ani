from __future__ import annotations

import asyncio

from openlist_ani.integrations.messaging.models import OutboundTarget
from openlist_ani.integrations.messaging.wechat_setup import (
    build_wechat_config_report,
    run_wechat_login,
)


async def test_run_wechat_login_returns_credentials_and_first_chat_id(monkeypatch):
    async def fake_qr_login(self, timeout_seconds=480):
        await asyncio.sleep(0)
        return {
            "account_id": "bot@im.bot",
            "token": "token",
            "base_url": "https://wx",
            "user_id": "user@im.wechat",
        }

    async def fake_wait_for_first_message(self, timeout_seconds=300):
        await asyncio.sleep(0)
        return OutboundTarget(
            platform="wechat",
            chat_id="notify@im.wechat",
            chat_type="dm",
            user_id="notify@im.wechat",
        )

    monkeypatch.setattr(
        "openlist_ani.integrations.messaging.wechat_setup.WechatIlinkMessenger.qr_login",
        fake_qr_login,
    )
    monkeypatch.setattr(
        "openlist_ani.integrations.messaging.wechat_setup.WechatIlinkMessenger.wait_for_first_message",
        fake_wait_for_first_message,
    )

    result = await run_wechat_login()

    assert result["account_id"] == "bot@im.bot"
    assert result["token"] == "token"
    assert result["base_url"] == "https://wx"
    assert result["chat_id"] == "notify@im.wechat"


def test_build_wechat_config_report_contains_notification_and_assistant_config():
    report = build_wechat_config_report(
        {
            "account_id": "bot@im.bot",
            "token": "token",
            "base_url": "https://wx",
            "chat_id": "notify@im.wechat",
        }
    )

    assert 'account_id = "bot@im.bot"' in report
    assert 'token = "token"' in report
    assert 'base_url = "https://wx"' in report
    assert 'home_channel = "notify@im.wechat"' in report
    assert report.count("home_channel") == 2
    assert "allowed_users" not in report
    assert "[assistant.wechat]" in report
