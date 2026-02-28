import aiohttp

from .base import BotBase


class TelegramBot(BotBase):
    def __init__(self, bot_token: str, user_id: str) -> None:
        self.bot_token = bot_token
        self.user_id = user_id
        self.support_markdown = True

    async def send_message(self, message: str) -> bool:
        """Send message via Telegram"""
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = {"chat_id": self.user_id, "text": message, "parse_mode": "HTML"}
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.post(api_url, json=body) as response:
                response.raise_for_status()
        return True
