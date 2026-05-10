from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from openlist_ani.logger import logger

from .models import InboundMessage, OutboundTarget
from .state_store import MessagingStateStore

FEISHU_BASE_URLS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}
WEBSOCKET_STARTUP_ERROR_GRACE_SECONDS = 3.0

InboundHandler = Callable[[InboundMessage], Awaitable[None]]


def infer_receive_id_type(receive_id: str) -> str:
    if receive_id.startswith("oc_"):
        return "chat_id"
    if receive_id.startswith("ou_"):
        return "open_id"
    if "@" in receive_id:
        return "email"
    return "open_id"


def _base_url(domain: str) -> str:
    return FEISHU_BASE_URLS.get(domain, FEISHU_BASE_URLS["feishu"])


def _read_field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _sender_user_id(sender: Any) -> str:
    sender_id = _read_field(sender, "sender_id", {}) or {}
    return str(
        _read_field(sender_id, "open_id") or _read_field(sender_id, "user_id") or ""
    )


def _strip_mentions(text: str, mentions: list[Any]) -> str:
    cleaned = text
    for mention in mentions:
        key = str(_read_field(mention, "key") or "")
        if key:
            cleaned = cleaned.replace(key, "")
    return cleaned.strip()


def _feishu_target(*, chat_id: str, user_id: str, is_group: bool) -> OutboundTarget:
    return OutboundTarget(
        platform="feishu",
        chat_id=chat_id if is_group else user_id,
        chat_type="group" if is_group else "dm",
        user_id=user_id,
        receive_id_type="chat_id" if is_group else "open_id",
    )


def parse_webhook_event(
    payload: dict[str, Any],
    *,
    bot_open_id: str = "",
    require_mention: bool = True,
) -> InboundMessage | None:
    header = payload.get("header") or {}
    if header.get("event_type") != "im.message.receive_v1":
        return None
    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    user_id = _sender_user_id(sender)
    chat_id = str(message.get("chat_id") or "")
    chat_type = str(message.get("chat_type") or "p2p")
    if not chat_id or not user_id:
        return None

    mentions = message.get("mentions") or []
    is_group = chat_type != "p2p"
    if is_group and require_mention and not _mentions_bot(mentions, bot_open_id):
        return None

    text = _strip_mentions(_extract_text(message.get("content")), mentions)
    if not text:
        return None

    return InboundMessage(
        platform="feishu",
        text=text,
        target=_feishu_target(
            chat_id=chat_id,
            user_id=user_id,
            is_group=is_group,
        ),
        message_id=str(message.get("message_id") or "") or None,
        raw=payload,
    )


def _mentions_bot(mentions: list[dict[str, Any]], bot_open_id: str) -> bool:
    if not mentions:
        return False
    if not bot_open_id:
        return True
    for mention in mentions:
        raw_id = _read_field(mention, "id")
        if _read_field(raw_id, "open_id") == bot_open_id:
            return True
    return False


def _extract_text(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return content
    elif isinstance(content, dict):
        payload = content
    else:
        return ""
    return str(payload.get("text") or "").strip()


class FeishuMessenger:
    platform = "feishu"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        store: MessagingStateStore | None = None,
        domain: str = "feishu",
        connection_mode: str = "websocket",
        webhook_host: str = "127.0.0.1",
        webhook_port: int = 8765,
        webhook_path: str = "/feishu/webhook",
        bot_open_id: str = "",
        require_mention: bool = True,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self.base_url = _base_url(domain)
        self.connection_mode = connection_mode
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.webhook_path = webhook_path
        self.bot_open_id = bot_open_id
        self.require_mention = require_mention
        self.store = store
        self._sdk_client: Any | None = None
        self._seen_message_ids: set[str] = set()

    async def send_text(
        self,
        receive_id: str | None,
        text: str,
        *,
        receive_id_type: str | None = None,
    ) -> bool:
        target_receive_id = receive_id
        target_type = receive_id_type
        if not target_receive_id:
            target = (
                self.store.load_notification_target("feishu") if self.store else None
            )
            if target:
                target_receive_id = target.chat_id
                target_type = target.receive_id_type
        if not target_receive_id:
            raise ValueError(
                "Feishu receive_id is required or must be bound by /set-notify-home"
            )
        target_type = target_type or infer_receive_id_type(target_receive_id)
        client = self._get_sdk_client()
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Feishu messaging requires the lark-oapi package"
            ) from exc

        request = (
            CreateMessageRequest.builder()
            .receive_id_type(target_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(target_receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = await client.im.v1.message.acreate(request)
        if response.success():
            return True
        logger.warning(
            "Feishu message send failed: code={}, msg={}, log_id={}",
            getattr(response, "code", None),
            getattr(response, "msg", None),
            response.get_log_id() if hasattr(response, "get_log_id") else None,
        )
        return False

    def _get_sdk_client(self) -> Any:
        if self._sdk_client is not None:
            return self._sdk_client
        try:
            import lark_oapi as lark
        except ImportError as exc:
            raise RuntimeError(
                "Feishu messaging requires the lark-oapi package"
            ) from exc

        self._sdk_client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(self.base_url)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        return self._sdk_client

    async def listen(self, handler: InboundHandler) -> None:
        if self.connection_mode == "webhook":
            await self._listen_webhook(handler)
            return
        if self.connection_mode == "websocket":
            await self._listen_websocket(handler)
            return
        raise ValueError(f"Unsupported Feishu connection_mode: {self.connection_mode}")

    async def _listen_webhook(self, handler: InboundHandler) -> None:
        app = web.Application()

        async def handle(request: web.Request) -> web.Response:
            payload = await request.json()
            if payload.get("type") == "url_verification":
                return web.json_response({"challenge": payload.get("challenge")})
            inbound = parse_webhook_event(
                payload,
                bot_open_id=self.bot_open_id,
                require_mention=self.require_mention,
            )
            if inbound and not self._is_duplicate(inbound.message_id):
                await handler(inbound)
            return web.json_response({"code": 0})

        app.router.add_post(self.webhook_path, handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.webhook_host, self.webhook_port)
        await site.start()
        logger.info(
            f"Feishu webhook listening on "
            f"{self.webhook_host}:{self.webhook_port}{self.webhook_path}"
        )
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()

    async def _listen_websocket(self, handler: InboundHandler) -> None:
        loop = asyncio.get_running_loop()
        startup_error: asyncio.Future[None] = loop.create_future()
        report_error = self._make_websocket_error_reporter(loop, startup_error)
        thread = threading.Thread(
            target=self._run_lark_ws_client,
            args=(handler, loop, report_error),
            name="feishu-websocket-listener",
            daemon=True,
        )
        thread.start()

        try:
            await asyncio.wait_for(
                asyncio.shield(startup_error),
                timeout=WEBSOCKET_STARTUP_ERROR_GRACE_SECONDS,
            )
        except TimeoutError:
            startup_error.add_done_callback(lambda future: future.exception())

        while True:
            await asyncio.sleep(3600)

    def _make_websocket_error_reporter(
        self,
        loop: asyncio.AbstractEventLoop,
        startup_error: asyncio.Future[None],
    ) -> Callable[[Exception], None]:
        def report_error(exc: Exception) -> None:
            def set_or_log_error() -> None:
                if startup_error.done():
                    logger.error(f"Feishu websocket listener stopped: {exc}")
                    return
                startup_error.set_exception(exc)

            loop.call_soon_threadsafe(set_or_log_error)

        return report_error

    def _run_lark_ws_client(
        self,
        handler: InboundHandler,
        loop: asyncio.AbstractEventLoop,
        report_error: Callable[[Exception], None],
    ) -> None:
        try:
            import lark_oapi as lark
            from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
            from lark_oapi.ws import Client as LarkWSClient
        except ImportError:
            report_error(
                RuntimeError("Feishu websocket mode requires the lark-oapi package")
            )
            return

        event_handler = self._build_lark_event_handler(
            EventDispatcherHandler,
            lambda data: self._handle_lark_ws_message(data, handler, loop),
        )
        try:
            client = LarkWSClient(
                self.app_id,
                self.app_secret,
                event_handler=event_handler,
                domain=self.base_url,
                log_level=lark.LogLevel.WARNING,
            )
            client.start()
        except Exception as exc:
            report_error(exc)

    def _build_lark_event_handler(
        self, dispatcher_handler: Any, on_message: Callable[[Any], None]
    ) -> Any:
        builder = dispatcher_handler.builder("", "").register_p2_im_message_receive_v1(
            on_message
        )
        if hasattr(builder, "register_p2_im_message_message_read_v1"):
            builder = builder.register_p2_im_message_message_read_v1(
                _ignore_message_read
            )
        return builder.build()

    def _handle_lark_ws_message(
        self,
        data: Any,
        handler: InboundHandler,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        payload = _payload_from_lark_ws_message(data)
        if payload is None:
            return
        inbound = parse_webhook_event(
            payload,
            bot_open_id=self.bot_open_id,
            require_mention=self.require_mention,
        )
        if inbound and not self._is_duplicate(inbound.message_id):
            asyncio.run_coroutine_threadsafe(handler(inbound), loop)

    def _is_duplicate(self, message_id: str | None) -> bool:
        if not message_id:
            return False
        if message_id in self._seen_message_ids:
            return True
        self._seen_message_ids.add(message_id)
        if len(self._seen_message_ids) > 2048:
            self._seen_message_ids = set(list(self._seen_message_ids)[-1024:])
        return False


def _ignore_message_read(_data: Any) -> bool:
    return False


def _payload_from_lark_ws_message(data: Any) -> dict[str, Any] | None:
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    if not event or not message or not sender:
        return None
    return {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "message_id": getattr(message, "message_id", ""),
                "chat_id": getattr(message, "chat_id", ""),
                "chat_type": getattr(message, "chat_type", "p2p"),
                "content": getattr(message, "content", ""),
                "mentions": getattr(message, "mentions", []) or [],
            },
            "sender": {
                "sender_id": getattr(sender, "sender_id", {}) or {},
                "sender_type": getattr(sender, "sender_type", "user"),
            },
        },
    }
