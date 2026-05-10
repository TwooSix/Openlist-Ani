from __future__ import annotations

import asyncio
import base64
import json
import secrets
import struct
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .models import InboundMessage, OutboundTarget

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0

EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"

LONG_POLL_TIMEOUT_SECONDS = 35.0
HTTP_TIMEOUT_SECONDS = 15.0

InboundHandler = Callable[[InboundMessage], Awaitable[None]]


def select_qr_scan_payload(qrcode_value: str, qrcode_img_content: str) -> str:
    """Return the value that should be encoded into the terminal QR code."""
    return qrcode_img_content or qrcode_value


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _headers(token: str | None, body: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if body:
        headers["Content-Length"] = str(len(body.encode("utf-8")))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(
        {**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_json_response_body(content: bytes) -> dict[str, Any]:
    payload = json.loads(content.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise TypeError("WeChat iLink API response must be a JSON object")
    return payload


def _print_qr_code(qrcode_value: str, qrcode_img_content: str) -> None:
    qr_scan_data = select_qr_scan_payload(qrcode_value, qrcode_img_content)
    print("\nScan this QR code with WeChat:")
    if qrcode_img_content:
        print(qrcode_img_content)
    try:
        import qrcode

        qr = qrcode.QRCode()
        qr.add_data(qr_scan_data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as exc:  # noqa: BLE001
        print(
            "Terminal QR rendering failed: "
            f"{exc}. Scan the URL above or install the qrcode dependency."
        )


def _next_qr_base_url(status: dict[str, Any], current_base_url: str) -> str:
    if status.get("status") == "scaned_but_redirect" and status.get("redirect_host"):
        return f"https://{status['redirect_host']}"
    return current_base_url


def _credentials_from_qr_status(
    status: dict[str, Any], base_url: str
) -> dict[str, str] | None:
    if status.get("status") != "confirmed":
        return None
    account_id = str(status.get("ilink_bot_id") or "")
    token = str(status.get("bot_token") or "")
    if not account_id or not token:
        raise RuntimeError("WeChat QR confirmed without bot credentials")
    return {
        "account_id": account_id,
        "token": token,
        "base_url": str(status.get("baseurl") or base_url),
        "user_id": str(status.get("ilink_user_id") or ""),
    }


async def api_get(
    *, endpoint: str, base_url: str = ILINK_BASE_URL, timeout_seconds: float = 15.0
) -> dict[str, Any]:
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(
            f"{base_url.rstrip('/')}/{endpoint}",
            headers=_headers(None),
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            response.raise_for_status()
            return decode_json_response_body(await response.read())


async def api_post(
    *,
    endpoint: str,
    payload: dict[str, Any],
    token: str,
    base_url: str,
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    body = _json_payload(payload)
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.post(
            f"{base_url.rstrip('/')}/{endpoint}",
            data=body,
            headers=_headers(token, body),
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as response:
            response.raise_for_status()
            return decode_json_response_body(await response.read())


def build_send_text_payload(
    *,
    to_user_id: str,
    text: str,
    context_token: str | None,
    client_id: str | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id or f"openlist-ani-wechat-{uuid.uuid4().hex}",
        "message_type": 2,
        "message_state": 2,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }
    if context_token:
        msg["context_token"] = context_token
    return {"msg": msg}


def _extract_text(item_list: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in item_list:
        if item.get("type") == 1:
            text = (item.get("text_item") or {}).get("text")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def parse_inbound_message(
    raw: dict[str, Any], *, account_id: str
) -> InboundMessage | None:
    sender_id = str(raw.get("from_user_id") or "").strip()
    if not sender_id or sender_id == account_id:
        return None
    item_list = raw.get("item_list") or []
    text = _extract_text(item_list)
    if not text:
        return None
    target = OutboundTarget(
        platform="wechat",
        chat_id=sender_id,
        chat_type="dm",
        user_id=sender_id,
    )
    return InboundMessage(
        platform="wechat",
        text=text,
        target=target,
        message_id=str(raw.get("message_id") or "") or None,
        raw=raw,
    )


class WechatIlinkMessenger:
    platform = "wechat"

    def __init__(
        self,
        *,
        account_id: str = "",
        token: str = "",
        base_url: str = ILINK_BASE_URL,
        interactive_login: bool = True,
    ) -> None:
        self.account_id = account_id
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.interactive_login = interactive_login
        self._running = False
        self._context_tokens: dict[tuple[str, str], str] = {}

    async def ensure_auth(self) -> dict[str, str]:
        account_id = self.account_id
        token = self.token
        base_url = self.base_url or ILINK_BASE_URL
        if account_id and token:
            self.account_id = account_id
            self.token = token
            self.base_url = base_url.rstrip("/")
            return {"account_id": account_id, "token": token, "base_url": self.base_url}

        if not self.interactive_login:
            raise RuntimeError(
                "WeChat auth is missing. Run openlist-ani-wechat-login first, "
                "or set account_id/token in the WeChat config."
            )

        credentials = await self.qr_login()
        self.account_id = credentials["account_id"]
        self.token = credentials["token"]
        self.base_url = credentials.get("base_url", ILINK_BASE_URL).rstrip("/")
        return credentials

    async def qr_login(self, timeout_seconds: int = 480) -> dict[str, str]:
        qr_resp = await api_get(endpoint=f"{EP_GET_BOT_QR}?bot_type=3")
        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_img_content = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            raise RuntimeError("WeChat QR response did not include qrcode")

        _print_qr_code(qrcode_value, qrcode_img_content)
        deadline = time.monotonic() + timeout_seconds
        base_url = ILINK_BASE_URL
        while time.monotonic() < deadline:
            status = await api_get(
                endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                base_url=base_url,
                timeout_seconds=LONG_POLL_TIMEOUT_SECONDS,
            )
            state = str(status.get("status") or "wait")
            if state == "scaned":
                print("QR code scanned. Confirm login in WeChat.")
            base_url = _next_qr_base_url(status, base_url)
            credentials = _credentials_from_qr_status(status, base_url)
            if credentials is not None:
                return credentials
            await asyncio.sleep(1)
        raise TimeoutError("WeChat QR login timed out")

    async def listen(self, handler: InboundHandler) -> None:
        await self.ensure_auth()
        self._running = True
        sync_buf = ""
        while self._running:
            response = await api_post(
                endpoint=EP_GET_UPDATES,
                payload={"get_updates_buf": sync_buf},
                token=self.token,
                base_url=self.base_url,
                timeout_seconds=LONG_POLL_TIMEOUT_SECONDS + 5,
            )
            sync_buf = str(response.get("get_updates_buf") or sync_buf)
            for raw in response.get("msgs") or []:
                self._remember_context_token(raw)
                inbound = parse_inbound_message(raw, account_id=self.account_id)
                if inbound:
                    await handler(inbound)

    def _remember_context_token(self, raw: dict[str, Any]) -> None:
        sender_id = str(raw.get("from_user_id") or "").strip()
        context_token = str(raw.get("context_token") or "").strip()
        if sender_id and context_token:
            self._context_tokens[(self.account_id, sender_id)] = context_token

    async def wait_for_first_message(
        self, *, timeout_seconds: int = 300
    ) -> OutboundTarget:
        await self.ensure_auth()
        print("Send any text message to this WeChat bot to capture chat_id.")
        sync_buf = ""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = await api_post(
                endpoint=EP_GET_UPDATES,
                payload={"get_updates_buf": sync_buf},
                token=self.token,
                base_url=self.base_url,
                timeout_seconds=min(LONG_POLL_TIMEOUT_SECONDS + 5, timeout_seconds),
            )
            sync_buf = str(response.get("get_updates_buf") or sync_buf)
            for raw in response.get("msgs") or []:
                sender_id = str(raw.get("from_user_id") or "").strip()
                if sender_id and sender_id != self.account_id:
                    return OutboundTarget(
                        platform="wechat",
                        chat_id=sender_id,
                        chat_type="dm",
                        user_id=sender_id,
                    )
        raise TimeoutError("Timed out waiting for WeChat message")

    async def send_text(self, chat_id: str | None, text: str) -> bool:
        await self.ensure_auth()
        target_chat_id = chat_id
        if not target_chat_id:
            raise ValueError(
                "WeChat notification target is missing. Run "
                "openlist-ani-wechat-login or set chat_id/home_channel in config."
            )

        context_token = self._context_tokens.get((self.account_id, target_chat_id))
        payload = build_send_text_payload(
            to_user_id=target_chat_id,
            text=text,
            context_token=context_token,
        )
        response = await api_post(
            endpoint=EP_SEND_MESSAGE,
            payload=payload,
            token=self.token,
            base_url=self.base_url,
            timeout_seconds=HTTP_TIMEOUT_SECONDS,
        )
        ret = response.get("ret", response.get("errcode", 0))
        return ret in (0, None)
