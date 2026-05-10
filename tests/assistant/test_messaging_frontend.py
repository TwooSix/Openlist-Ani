from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from openlist_ani.assistant.core.context import ContextBuilder
from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.core.models import ProviderResponse
from openlist_ani.assistant.frontend.messaging import (
    AllowedChatAuthorizer,
    MessagingFrontend,
)
from openlist_ani.assistant.memory.manager import MemoryManager
from openlist_ani.assistant.session.storage import SessionStorage
from openlist_ani.assistant.tool.registry import ToolRegistry
from openlist_ani.integrations.messaging.state_store import MessagingStateStore
from openlist_ani.integrations.messaging.models import InboundMessage, OutboundTarget
from .conftest import MockProvider


class FakeMessenger:
    platform = "wechat"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.handler = None

    async def listen(self, handler):
        await asyncio.sleep(0)
        self.handler = handler

    async def send_text(self, chat_id: str | None, text: str) -> bool:
        await asyncio.sleep(0)
        self.sent.append((chat_id or "", text))
        return True


def _make_loop(tmp: Path, response: str = "assistant reply") -> AgenticLoop:
    provider = MockProvider([ProviderResponse(text=response)])
    registry = ToolRegistry()
    memory = MemoryManager(data_dir=tmp / "data", project_root=tmp / "proj")
    (tmp / "proj").mkdir(exist_ok=True)
    context = ContextBuilder(memory)
    return AgenticLoop(
        provider,
        registry,
        context,
        memory,
        session_storage=SessionStorage(tmp / "sessions"),
    )


def _message(text: str, chat_id: str = "chat-1") -> InboundMessage:
    return InboundMessage(
        platform="wechat",
        text=text,
        target=OutboundTarget(
            platform="wechat",
            chat_id=chat_id,
            chat_type="dm",
            user_id="user-1",
        ),
        message_id="msg-1",
    )


@pytest.mark.asyncio
async def test_id_command_returns_chat_details(tmp_path):
    messenger = FakeMessenger()
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=_make_loop(tmp_path),
        loop_factory=lambda: _make_loop(tmp_path),
    )

    await frontend.handle_inbound(_message("/id"))

    assert "chat_id=chat-1" in messenger.sent[-1][1]
    assert "user_id=user-1" in messenger.sent[-1][1]


@pytest.mark.asyncio
async def test_set_notify_home_persists_current_target(tmp_path):
    store = MessagingStateStore(tmp_path / "state")
    messenger = FakeMessenger()
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=_make_loop(tmp_path),
        loop_factory=lambda: _make_loop(tmp_path),
        state_store=store,
    )

    await frontend.handle_inbound(_message("/set-notify-home"))

    assert store.load_notification_target("wechat").chat_id == "chat-1"
    assert "Notification target set" in messenger.sent[-1][1]


@pytest.mark.asyncio
async def test_set_notify_home_can_be_disabled(tmp_path):
    messenger = FakeMessenger()
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=_make_loop(tmp_path, response="ignored"),
        loop_factory=lambda: _make_loop(tmp_path, response="ignored"),
        enable_notify_home_command=False,
    )

    await frontend.handle_inbound(_message("/set-notify-home"))

    assert "Notification target set" not in messenger.sent[-1][1]


@pytest.mark.asyncio
async def test_processes_message_and_sends_final_response(tmp_path):
    messenger = FakeMessenger()
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=_make_loop(tmp_path, response="hello from ai"),
        loop_factory=lambda: _make_loop(tmp_path, response="hello from ai"),
    )

    await frontend.handle_inbound(_message("hi"))

    assert messenger.sent[-1] == ("chat-1", "hello from ai")


@pytest.mark.asyncio
async def test_wechat_authorization_uses_chat_id_home_channel(tmp_path):
    messenger = FakeMessenger()
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=_make_loop(tmp_path, response="hello from ai"),
        loop_factory=lambda: _make_loop(tmp_path, response="hello from ai"),
        authorizer=AllowedChatAuthorizer(["chat-1"]),
    )

    await frontend.handle_inbound(_message("hi", chat_id="chat-1"))

    assert messenger.sent[-1] == ("chat-1", "hello from ai")


@pytest.mark.asyncio
async def test_wechat_rejects_messages_outside_home_channel(tmp_path):
    messenger = FakeMessenger()
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=_make_loop(tmp_path),
        loop_factory=lambda: _make_loop(tmp_path),
        authorizer=AllowedChatAuthorizer(["chat-1"]),
    )

    await frontend.handle_inbound(_message("hi", chat_id="chat-2"))

    assert messenger.sent[-1] == ("chat-2", "Unauthorized.")


@pytest.mark.asyncio
async def test_active_turn_enqueues_pending_message(tmp_path):
    messenger = FakeMessenger()
    loop = _make_loop(Path(tempfile.mkdtemp()), response="first")
    frontend = MessagingFrontend(
        platform="wechat",
        messenger=messenger,
        loop=loop,
        loop_factory=lambda: loop,
    )
    frontend._active_turns.add("wechat:chat-1:user-1")

    await frontend.handle_inbound(_message("interrupt"))

    assert loop.message_queue.has_pending_prompts() is True
