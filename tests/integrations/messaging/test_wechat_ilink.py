from __future__ import annotations

import asyncio

import pytest

from openlist_ani.integrations.messaging.wechat_ilink import (
    WechatIlinkMessenger,
    decode_json_response_body,
    parse_inbound_message,
    select_qr_scan_payload,
)


def test_parse_inbound_message_extracts_text_and_target():
    raw = {
        "message_id": "msg-1",
        "from_user_id": "user@im.wechat",
        "to_user_id": "bot@im.bot",
        "context_token": "ctx-token",
        "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
    }

    inbound = parse_inbound_message(raw, account_id="bot@im.bot")

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.target.chat_id == "user@im.wechat"
    assert inbound.target.user_id == "user@im.wechat"


def test_select_qr_scan_payload_prefers_scannable_url():
    assert (
        select_qr_scan_payload("qr-token", "https://open.weixin.qq.com/qr")
        == "https://open.weixin.qq.com/qr"
    )
    assert select_qr_scan_payload("qr-token", "") == "qr-token"


def test_decode_json_response_body_accepts_octet_stream_json():
    payload = decode_json_response_body(
        b'{"qrcode":"qr-token","qrcode_img_content":"https://qr.example"}'
    )

    assert payload == {
        "qrcode": "qr-token",
        "qrcode_img_content": "https://qr.example",
    }


@pytest.mark.asyncio
async def test_send_text_uses_in_memory_context_token(monkeypatch):
    sent_payloads = []

    async def fake_post(*, endpoint, payload, token, base_url, timeout_seconds):
        await asyncio.sleep(0)
        sent_payloads.append(payload)
        return {"ret": 0}

    monkeypatch.setattr(
        "openlist_ani.integrations.messaging.wechat_ilink.api_post", fake_post
    )
    messenger = WechatIlinkMessenger(
        account_id="bot@im.bot",
        token="token",
        base_url="https://wx",
        interactive_login=False,
    )
    messenger._remember_context_token(
        {"from_user_id": "user@im.wechat", "context_token": "ctx-token"}
    )

    assert await messenger.send_text("user@im.wechat", "hello") is True
    assert sent_payloads[0]["msg"]["to_user_id"] == "user@im.wechat"
    assert sent_payloads[0]["msg"]["context_token"] == "ctx-token"


@pytest.mark.asyncio
async def test_send_text_uses_configured_chat_id(monkeypatch):
    sent_payloads = []

    async def fake_post(*, endpoint, payload, token, base_url, timeout_seconds):
        await asyncio.sleep(0)
        sent_payloads.append(payload)
        return {"ret": 0}

    monkeypatch.setattr(
        "openlist_ani.integrations.messaging.wechat_ilink.api_post", fake_post
    )
    messenger = WechatIlinkMessenger(
        account_id="bot@im.bot",
        token="token",
        base_url="https://wx",
        interactive_login=False,
    )

    assert await messenger.send_text("configured@im.wechat", "hello") is True
    assert sent_payloads[0]["msg"]["to_user_id"] == "configured@im.wechat"


@pytest.mark.asyncio
async def test_send_text_requires_explicit_target():
    messenger = WechatIlinkMessenger(
        account_id="bot@im.bot",
        token="token",
        base_url="https://wx",
        interactive_login=False,
    )

    with pytest.raises(ValueError, match="home_channel"):
        await messenger.send_text(None, "hello")


@pytest.mark.asyncio
async def test_wait_for_first_message_returns_first_inbound_target(monkeypatch):
    messenger = WechatIlinkMessenger(
        account_id="bot@im.bot",
        token="token",
        base_url="https://wx",
        interactive_login=False,
    )

    async def fake_post(*, endpoint, payload, token, base_url, timeout_seconds):
        await asyncio.sleep(0)
        return {
            "get_updates_buf": "next",
            "msgs": [
                {
                    "message_id": "msg-1",
                    "from_user_id": "notify@im.wechat",
                    "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
                }
            ],
        }

    monkeypatch.setattr(
        "openlist_ani.integrations.messaging.wechat_ilink.api_post", fake_post
    )

    target = await messenger.wait_for_first_message(timeout_seconds=1)

    assert target.chat_id == "notify@im.wechat"
    assert target.user_id == "notify@im.wechat"
