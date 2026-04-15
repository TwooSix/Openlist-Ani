"""Tests for TelegramBot."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from openlist_ani.core.notification.bot.base import BotBase
from openlist_ani.core.notification.bot.telegram import TelegramBot


class TestTelegramBot:
    def test_init(self):
        bot = TelegramBot(bot_token="tok123", user_id=42)
        assert bot.bot_token == "tok123"
        assert bot.user_id == 42

    def test_is_instance_of_base(self):
        bot = TelegramBot(bot_token="t", user_id=1)
        assert isinstance(bot, BotBase)


class TestTelegramBotSendMessage:
    """Tests for TelegramBot.send_message() with mocked HTTP."""

    async def test_send_message_success(self):
        """Verify message is sent with correct URL, payload, and returns True."""
        bot = TelegramBot(bot_token="fake-token", user_id="12345")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                aiohttp,
                "ClientSession",
                MagicMock(return_value=mock_session),
            )
            result = await bot.send_message("Hello, world!")

        assert result is True

        # Verify the POST was made to the correct Telegram API URL
        mock_session.post.assert_called_once_with(
            "https://api.telegram.org/botfake-token/sendMessage",
            json={
                "chat_id": "12345",
                "text": "Hello, world!",
                "parse_mode": "HTML",
            },
        )

    async def test_send_message_http_error(self):
        """Verify aiohttp.ClientResponseError propagates on HTTP failure."""
        bot = TelegramBot(bot_token="fake-token", user_id="12345")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=500,
                message="Internal Server Error",
            )
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                aiohttp,
                "ClientSession",
                MagicMock(return_value=mock_session),
            )
            with pytest.raises(aiohttp.ClientResponseError) as exc_info:
                await bot.send_message("This will fail")

        assert exc_info.value.status == 500
