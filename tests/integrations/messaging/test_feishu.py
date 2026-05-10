from __future__ import annotations

import asyncio
import json
import sys
import threading
import types

import pytest

from openlist_ani.integrations.messaging.state_store import MessagingStateStore
from openlist_ani.integrations.messaging.feishu import (
    FeishuMessenger,
    infer_receive_id_type,
    parse_webhook_event,
)


def test_infer_receive_id_type_from_prefixes():
    assert infer_receive_id_type("oc_abc") == "chat_id"
    assert infer_receive_id_type("ou_abc") == "open_id"
    assert infer_receive_id_type("user@example.com") == "email"
    assert infer_receive_id_type("custom") == "open_id"


def test_feishu_messenger_defaults_to_websocket_mode(tmp_path):
    messenger = FeishuMessenger(
        app_id="cli_xxx",
        app_secret="secret",
        store=MessagingStateStore(tmp_path),
    )

    assert messenger.connection_mode == "websocket"


def test_parse_webhook_event_normalizes_text_and_target():
    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "group",
                "content": json.dumps({"text": "@_user_1 hello"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot"}}],
            },
            "sender": {
                "sender_id": {"open_id": "ou_user"},
                "sender_type": "user",
            },
        },
    }

    inbound = parse_webhook_event(payload, bot_open_id="ou_bot")

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.target.chat_id == "oc_1"
    assert inbound.target.chat_type == "group"
    assert inbound.target.user_id == "ou_user"


def test_parse_webhook_event_accepts_lark_sdk_model_objects():
    class UserId:
        def __init__(self, open_id):
            self.open_id = open_id
            self.user_id = None
            self.union_id = None

    class Mention:
        def __init__(self):
            self.key = "@_user_1"
            self.id = UserId("ou_bot")

    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "group",
                "content": json.dumps({"text": "@_user_1 hello"}),
                "mentions": [Mention()],
            },
            "sender": {
                "sender_id": UserId("ou_user"),
                "sender_type": "user",
            },
        },
    }

    inbound = parse_webhook_event(payload, bot_open_id="ou_bot")

    assert inbound is not None
    assert inbound.text == "hello"
    assert inbound.target.chat_id == "oc_1"
    assert inbound.target.user_id == "ou_user"


@pytest.mark.asyncio
async def test_send_text_uses_official_lark_sdk(tmp_path, monkeypatch):
    store = MessagingStateStore(tmp_path)
    sent = {}

    class FakeClientBuilder:
        def app_id(self, value):
            sent["app_id"] = value
            return self

        def app_secret(self, value):
            sent["app_secret"] = value
            return self

        def domain(self, value):
            sent["domain"] = value
            return self

        def log_level(self, value):
            sent["log_level"] = value
            return self

        def build(self):
            return FakeClient()

    class FakeClientFactory:
        @staticmethod
        def builder():
            return FakeClientBuilder()

    class FakeMessage:
        async def acreate(self, request):
            await asyncio.sleep(0)
            sent["request"] = request
            return types.SimpleNamespace(success=lambda: True)

    class FakeClient:
        def __init__(self):
            self.im = types.SimpleNamespace(
                v1=types.SimpleNamespace(message=FakeMessage())
            )

    class FakeRequestBodyBuilder:
        def __init__(self):
            self.body = {}

        def receive_id(self, value):
            self.body["receive_id"] = value
            return self

        def msg_type(self, value):
            self.body["msg_type"] = value
            return self

        def content(self, value):
            self.body["content"] = value
            return self

        def build(self):
            return dict(self.body)

    class FakeRequestBody:
        @staticmethod
        def builder():
            return FakeRequestBodyBuilder()

    class FakeRequestBuilder:
        def __init__(self):
            self.request = {}

        def receive_id_type(self, value):
            self.request["receive_id_type"] = value
            return self

        def request_body(self, value):
            self.request["body"] = value
            return self

        def build(self):
            return dict(self.request)

    class FakeRequest:
        @staticmethod
        def builder():
            return FakeRequestBuilder()

    monkeypatch.setitem(
        sys.modules,
        "lark_oapi",
        types.SimpleNamespace(
            Client=FakeClientFactory,
            LogLevel=types.SimpleNamespace(WARNING="warning"),
        ),
    )
    monkeypatch.setitem(sys.modules, "lark_oapi.api", types.ModuleType("api"))
    monkeypatch.setitem(sys.modules, "lark_oapi.api.im", types.ModuleType("im"))
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.api.im.v1",
        types.SimpleNamespace(
            CreateMessageRequest=FakeRequest,
            CreateMessageRequestBody=FakeRequestBody,
        ),
    )
    messenger = FeishuMessenger(
        app_id="cli_xxx",
        app_secret="secret",
        store=store,
    )

    assert await messenger.send_text("oc_1", "hello") is True
    assert sent["request"]["receive_id_type"] == "chat_id"
    assert sent["request"]["body"]["receive_id"] == "oc_1"
    assert json.loads(sent["request"]["body"]["content"]) == {"text": "hello"}


@pytest.mark.asyncio
async def test_websocket_client_is_constructed_inside_worker_thread(
    tmp_path, monkeypatch
):
    constructed_clients = []
    main_thread_id = threading.get_ident()

    class FakeBuilder:
        def register_p2_im_message_receive_v1(self, callback):
            self.callback = callback
            return self

        def register_p2_im_message_message_read_v1(self, callback):
            self.read_callback = callback
            return self

        def build(self):
            return {"callback": self.callback}

    class FakeDispatcherHandler:
        @staticmethod
        def builder(encrypt_key, verification_token):
            return FakeBuilder()

    class FakeClient:
        def __init__(self, app_id, app_secret, *, event_handler, domain, log_level):
            constructed_clients.append(
                {
                    "app_id": app_id,
                    "app_secret": app_secret,
                    "event_handler": event_handler,
                    "domain": domain,
                    "log_level": log_level,
                    "thread_id": threading.get_ident(),
                }
            )

        def start(self):
            raise RuntimeError("stop fake websocket")

    monkeypatch.setitem(
        sys.modules,
        "lark_oapi",
        types.SimpleNamespace(LogLevel=types.SimpleNamespace(WARNING="warning")),
    )
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.event.dispatcher_handler",
        types.SimpleNamespace(EventDispatcherHandler=FakeDispatcherHandler),
    )
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.ws",
        types.SimpleNamespace(Client=FakeClient),
    )

    messenger = FeishuMessenger(
        app_id="cli_xxx",
        app_secret="secret",
        store=MessagingStateStore(tmp_path),
    )

    with pytest.raises(RuntimeError, match="stop fake websocket"):
        await messenger._listen_websocket(lambda message: None)

    assert constructed_clients
    assert constructed_clients[0]["thread_id"] != main_thread_id


@pytest.mark.asyncio
async def test_websocket_listener_cancel_does_not_wait_for_blocking_sdk(
    tmp_path, monkeypatch
):
    started = threading.Event()
    stop = threading.Event()

    class FakeBuilder:
        def register_p2_im_message_receive_v1(self, callback):
            return self

        def register_p2_im_message_message_read_v1(self, callback):
            return self

        def build(self):
            return {}

    class FakeDispatcherHandler:
        @staticmethod
        def builder(encrypt_key, verification_token):
            return FakeBuilder()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            started.set()
            stop.wait(timeout=5)

    monkeypatch.setitem(
        sys.modules,
        "lark_oapi",
        types.SimpleNamespace(LogLevel=types.SimpleNamespace(WARNING="warning")),
    )
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.event.dispatcher_handler",
        types.SimpleNamespace(EventDispatcherHandler=FakeDispatcherHandler),
    )
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.ws",
        types.SimpleNamespace(Client=FakeClient),
    )
    monkeypatch.setattr(
        "openlist_ani.integrations.messaging.feishu.WEBSOCKET_STARTUP_ERROR_GRACE_SECONDS",
        0.01,
    )
    messenger = FeishuMessenger(
        app_id="cli_xxx",
        app_secret="secret",
        store=MessagingStateStore(tmp_path),
    )
    task = asyncio.create_task(messenger._listen_websocket(lambda message: None))
    await asyncio.to_thread(started.wait, 1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stop.set()
