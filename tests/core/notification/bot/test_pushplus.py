"""Tests for PushPlusBot and PushPlusChannel."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from openlist_ani.core.notification.bot.base import BotBase
from openlist_ani.core.notification.bot.pushplus import PushPlusBot, PushPlusChannel


class TestPushPlusBot:
    def test_init_defaults_to_wechat(self):
        bot = PushPlusBot(user_token="tok")
        assert bot.channel == PushPlusChannel.WECHAT

    def test_init_with_valid_channel(self):
        bot = PushPlusBot(user_token="tok", channel="webhook")
        assert bot.channel == PushPlusChannel.WEBHOOK

    def test_init_with_invalid_channel_raises(self):
        with pytest.raises(ValueError, match="Invalid channel"):
            PushPlusBot(user_token="tok", channel="invalid_channel")

    def test_is_instance_of_base(self):
        bot = PushPlusBot(user_token="tok")
        assert isinstance(bot, BotBase)


class TestPushPlusChannel:
    def test_all_channels(self):
        assert PushPlusChannel.WECHAT.value == "wechat"
        assert PushPlusChannel.WEBHOOK.value == "webhook"
        assert PushPlusChannel.CP.value == "cp"
        assert PushPlusChannel.MAIL.value == "mail"


class TestPushPlusBotSendMessage:
    """Tests for PushPlusBot.send_message() with mocked HTTP."""

    async def test_send_message_success(self):
        """Verify correct payload is sent and True is returned on success."""
        bot = PushPlusBot(user_token="my-token", channel="wechat")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(
            return_value={"code": 200, "msg": "ok", "data": ""}
        )
        mock_resp.request_info = MagicMock()
        mock_resp.history = ()
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
            result = await bot.send_message("Test notification")

        assert result is True

        # Verify the POST was made to correct URL with correct payload
        mock_session.post.assert_called_once_with(
            "http://www.pushplus.plus/send/my-token",  # NOSONAR — matches real PushPlus API
            json={
                "title": "OpenList-Ani 通知",
                "content": "Test notification",
                "channel": "wechat",
                "template": "html",
            },
        )

    async def test_send_message_api_error_code(self):
        """Verify ClientResponseError is raised when API returns non-200 code."""
        bot = PushPlusBot(user_token="my-token")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()  # HTTP 200 OK
        mock_resp.json = AsyncMock(
            return_value={
                "code": 500,
                "msg": "",
                "message": "Token expired",
                "data": "",
            }
        )
        mock_resp.request_info = MagicMock()
        mock_resp.history = ()
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
        assert "Token expired" in exc_info.value.message

    async def test_send_message_api_error_unknown_message(self):
        """Verify fallback 'Unknown error' when API error has no message."""
        bot = PushPlusBot(user_token="my-token")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(
            return_value={"code": 400, "data": ""}
        )
        mock_resp.request_info = MagicMock()
        mock_resp.history = ()
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
                await bot.send_message("Fail")

        assert exc_info.value.status == 400
        assert "Unknown error" in exc_info.value.message

    async def test_send_message_http_error(self):
        """Verify HTTP-level errors (e.g. 503) propagate correctly."""
        bot = PushPlusBot(user_token="my-token")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=503,
                message="Service Unavailable",
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
                await bot.send_message("Fail")

        assert exc_info.value.status == 503
