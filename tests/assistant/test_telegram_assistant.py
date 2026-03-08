"""Tests for Telegram assistant integration helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openlist_ani.assistant.assistant import AssistantStatus
from openlist_ani.assistant.telegram_assistant import TelegramAssistant


class TestTelegramAssistant:
    def test_build_memory_key_uses_telegram_user_id(self):
        user = SimpleNamespace(id=12345)
        assert TelegramAssistant._build_memory_key(user) == "telegram:12345"

    async def test_authorize_user_rejects_unknown_user(self):
        fake_assistant = MagicMock()

        with patch(
            "openlist_ani.assistant.telegram_assistant.AniAssistant",
            return_value=fake_assistant,
        ):
            assistant = TelegramAssistant(download_manager=MagicMock())

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

    def test_format_status_text_for_download(self):
        status_text = TelegramAssistant._format_status_text(
            AssistantStatus.TOOL_EXECUTING,
            {"tool_name": "download_resource", "title": "Frieren - 01"},
        )

        assert "Frieren - 01" in status_text
