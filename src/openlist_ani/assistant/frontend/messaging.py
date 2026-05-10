from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from openlist_ani.assistant.core.message_queue import PendingMessage
from openlist_ani.assistant.core.models import EventType
from openlist_ani.assistant.frontend.base import Frontend
from openlist_ani.integrations.messaging.models import InboundMessage
from openlist_ani.integrations.messaging.state_store import MessagingStateStore

if TYPE_CHECKING:
    from openlist_ani.assistant.core.loop import AgenticLoop
    from openlist_ani.assistant.skill.catalog import SkillCatalog


class TextMessenger(Protocol):
    platform: str

    async def listen(self, handler): ...

    async def send_text(self, chat_id: str | None, text: str) -> bool: ...


class MessageAuthorizer(Protocol):
    def is_authorized(self, message: InboundMessage) -> bool: ...


class AllowedTargetAuthorizer:
    def __init__(
        self,
        allowed_values: Iterable[str],
        key: Callable[[InboundMessage], str],
    ) -> None:
        self._allowed_values = set(allowed_values)
        self._key = key

    def is_authorized(self, message: InboundMessage) -> bool:
        return not self._allowed_values or self._key(message) in self._allowed_values


class AllowedUserAuthorizer(AllowedTargetAuthorizer):
    def __init__(self, allowed_users: Iterable[str]) -> None:
        super().__init__(allowed_users, lambda message: message.target.user_id)


class AllowedChatAuthorizer(AllowedTargetAuthorizer):
    def __init__(self, allowed_chats: Iterable[str]) -> None:
        super().__init__(allowed_chats, lambda message: message.target.chat_id)


class MessagingFrontend(Frontend):
    """Generic text messaging frontend for WeChat and Feishu."""

    def __init__(
        self,
        *,
        platform: str,
        messenger: TextMessenger,
        loop: AgenticLoop,
        loop_factory: Callable[[], AgenticLoop] | None = None,
        state_store: MessagingStateStore | None = None,
        allowed_users: list[str] | None = None,
        authorizer: MessageAuthorizer | None = None,
        enable_notify_home_command: bool = True,
        catalog: SkillCatalog | None = None,
    ) -> None:
        super().__init__(loop)
        self.platform = platform
        self._messenger = messenger
        self._loop_factory = loop_factory
        self._state_store = state_store
        self._authorizer = authorizer or AllowedUserAuthorizer(allowed_users or [])
        self._enable_notify_home_command = enable_notify_home_command
        self._catalog = catalog
        self._chat_loops: dict[str, AgenticLoop] = {}
        self._active_turns: set[str] = set()

    async def run(self) -> None:
        await self._messenger.listen(self.handle_inbound)

    async def send_response(self, text: str) -> None:
        raise NotImplementedError("MessagingFrontend sends through inbound targets")

    async def handle_inbound(self, message: InboundMessage) -> None:
        if not self._authorizer.is_authorized(message):
            await self._messenger.send_text(message.target.chat_id, "Unauthorized.")
            return

        if await self._handle_builtin_command(message):
            return

        await self._process_user_turn(message, message.text)

    async def _process_user_turn(self, message: InboundMessage, text: str) -> None:
        session_key = self._session_key(message)
        loop = await self._get_loop(message)
        if session_key in self._active_turns:
            loop.message_queue.enqueue(PendingMessage(content=text))
            return

        self._active_turns.add(session_key)
        try:
            final_parts: list[str] = []
            async for event in loop.process(text):
                if event.type == EventType.TEXT_DONE and event.text:
                    final_parts.append(event.text)
                elif event.type == EventType.ERROR and event.text:
                    final_parts.append(f"Error: {event.text}")
            response = "\n".join(final_parts).strip() or "No response."
            await self._messenger.send_text(message.target.chat_id, response)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{self.platform} frontend failed: {exc}")
            await self._messenger.send_text(message.target.chat_id, f"Error: {exc}")
        finally:
            self._active_turns.discard(session_key)

    async def _get_loop(self, message: InboundMessage) -> AgenticLoop:
        session_key = self._session_key(message)
        if session_key in self._chat_loops:
            return self._chat_loops[session_key]

        loop = self._loop_factory() if self._loop_factory else self._loop
        self._chat_loops[session_key] = loop
        storage = loop.session_storage
        if storage is None:
            return loop

        existing = await storage.list_sessions()
        matching = [
            s
            for s in existing
            if s.metadata.get("frontend") == self.platform
            and s.metadata.get("chat_id") == message.target.chat_id
            and s.metadata.get("user_id") == message.target.user_id
        ]
        if matching:
            await loop.resume(matching[0].session_id)
        else:
            await storage.start_new_session(metadata=self._session_metadata(message))
        return loop

    async def _handle_builtin_command(self, message: InboundMessage) -> bool:
        text = message.text.strip()
        if text == "/id":
            lines = [
                f"platform={self.platform}",
                f"chat_id={message.target.chat_id}",
                f"chat_type={message.target.chat_type}",
                f"user_id={message.target.user_id}",
            ]
            if message.target.receive_id_type:
                lines.append(f"receive_id_type={message.target.receive_id_type}")
            await self._messenger.send_text(message.target.chat_id, "\n".join(lines))
            return True

        if text == "/set-notify-home" and self._enable_notify_home_command:
            if self._state_store is None:
                raise RuntimeError("Messaging state store is not configured")
            self._state_store.save_notification_target(self.platform, message.target)
            await self._messenger.send_text(
                message.target.chat_id,
                "Notification target set to the current conversation.",
            )
            return True

        if text == "/clear":
            loop = await self._get_loop(message)
            loop.reset()
            if loop.session_storage:
                await loop.session_storage.start_new_session(
                    metadata=self._session_metadata(message)
                )
            await self._messenger.send_text(
                message.target.chat_id, "New session started."
            )
            return True

        if text.startswith("/") and await self._handle_skill_command(message):
            return True

        return False

    async def _handle_skill_command(self, message: InboundMessage) -> bool:
        if self._catalog is None:
            return False
        parts = message.text.strip().split(None, 1)
        command = parts[0].lstrip("/").lower() if parts else ""
        user_text = parts[1] if len(parts) > 1 else ""
        skill = self._catalog.get_skill(command)
        if skill is None:
            skill = self._catalog.get_skill(command.replace("_", "-"))
        if skill is None:
            return False
        skill_content = self._catalog.get_skill_content(skill.name) or ""
        augmented = "\n".join(
            [
                f"<command-name>/{skill.name}</command-name>",
                f'<skill name="{skill.name}">',
                f"Base directory for this skill: {skill.base_dir}",
                "",
                skill_content,
                "</skill>",
                "",
                user_text,
            ]
        ).strip()
        await self._process_user_turn(message, augmented)
        return True

    def _session_key(self, message: InboundMessage) -> str:
        return f"{self.platform}:{message.target.chat_id}:{message.target.user_id}"

    def _session_metadata(self, message: InboundMessage) -> dict[str, object]:
        return {
            "frontend": self.platform,
            "chat_id": message.target.chat_id,
            "chat_type": message.target.chat_type,
            "user_id": message.target.user_id,
        }
