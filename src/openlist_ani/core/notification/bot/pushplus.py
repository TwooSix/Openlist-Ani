from enum import Enum

import aiohttp

from .base import BotBase


class PushPlusChannel(Enum):
    WECHAT = "wechat"
    WEBHOOK = "webhook"
    CP = "cp"
    MAIL = "mail"


class PushPlusBot(BotBase):
    def __init__(self, user_token: str, channel: str | None = None) -> None:
        self.user_token = user_token
        if channel:
            try:
                self.channel = PushPlusChannel(channel)
            except ValueError:
                raise ValueError(f"Invalid channel: {channel}")
        else:
            self.channel = PushPlusChannel.WECHAT

    async def send_message(self, message: str) -> bool:
        api_url = f"http://www.pushplus.plus/send/{self.user_token}"
        body = {
            "title": "OpenList-Ani 通知",
            "content": message,
            "channel": self.channel.value,
            "template": "html",
        }
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(api_url, json=body) as response:
                response.raise_for_status()
                data = await response.json()
                if data["code"] != 200:
                    error_code = data["code"]
                    error_message = data.get("message") or "Unknown error"
                    full_error_message = f"Error {error_code}: {error_message}"
                    raise aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=data["code"],
                        message=full_error_message,
                    )
        return True
