from __future__ import annotations

from openlist_ani.adapters.outbound.configuration.settings import (
    AssistantConfig,
    FeishuAssistantConfig,
    TelegramAssistantConfig,
    WechatAssistantConfig,
)
from openlist_ani.assistant import (
    _enabled_frontend_names,
    _validate_frontend_config,
)


def test_validate_frontend_config_requires_wechat_setup_output():
    cfg = AssistantConfig(wechat=WechatAssistantConfig(enabled=True))

    errors = _validate_frontend_config(cfg)

    assert any("account_id" in error for error in errors)
    assert any("token" in error for error in errors)
    assert any("home_channel" in error for error in errors)
    assert any("openlist-ani-wechat-login" in error for error in errors)


def test_validate_frontend_config_accepts_wechat_setup_output():
    cfg = AssistantConfig(
        wechat=WechatAssistantConfig(
            enabled=True,
            account_id="bot@im.bot",
            token="token",
            home_channel="user@im.wechat",
        )
    )

    assert _validate_frontend_config(cfg) == []


def test_validate_frontend_config_requires_feishu_app_credentials():
    cfg = AssistantConfig(feishu=FeishuAssistantConfig(enabled=True))

    errors = _validate_frontend_config(cfg)

    assert any("app_id" in error for error in errors)
    assert any("app_secret" in error for error in errors)


def test_enabled_frontend_names_allow_telegram_and_wechat_to_coexist():
    cfg = AssistantConfig(
        telegram=TelegramAssistantConfig(bot_token="token"),
        wechat=WechatAssistantConfig(
            enabled=True,
            account_id="bot@im.bot",
            token="wechat-token",
            home_channel="user@im.wechat",
        ),
    )

    assert _enabled_frontend_names(cfg) == ["telegram", "wechat"]
