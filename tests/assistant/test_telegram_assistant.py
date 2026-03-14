"""Tests for Telegram assistant integration helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openlist_ani.assistant.telegram_assistant import TelegramAssistant


class TestTelegramAssistant:
    async def test_authorize_user_rejects_unknown_user(self):
        fake_assistant = MagicMock()

        with patch(
            "openlist_ani.assistant.telegram_assistant.AniAssistant",
            return_value=fake_assistant,
        ):
            assistant = TelegramAssistant(backend_client=MagicMock())

        assistant.allowed_users = {1}
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=2),
            effective_chat=SimpleNamespace(id=99),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        result = await assistant._authorize_user(update, context)

        assert result is False
        context.bot.send_message.assert_awaited_once_with(
            chat_id=99,
            text=assistant.UNAUTHORIZED_MESSAGE,
        )

    async def test_send_chunked_message_short(self):
        """Short messages should be sent as-is."""
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await TelegramAssistant._send_chunked_message(context, 123, "Hello")
        context.bot.send_message.assert_awaited_once_with(chat_id=123, text="Hello")
