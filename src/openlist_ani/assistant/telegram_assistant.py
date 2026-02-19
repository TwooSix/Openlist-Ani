"""
Telegram bot integration for assistant.
"""

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

from ..config import config
from ..core.download import DownloadManager
from ..logger import logger
from .assistant import AniAssistant, AssistantStatus


class TelegramAssistant:
    """Telegram bot that integrates with AniAssistant."""

    WELCOME_MESSAGE = """👋 Hello! I'm an anime resource download assistant.

I can help you:
1️⃣ Download anime resources from RSS feeds
2️⃣ Search for resources on mikan.moe, dmhy, acg.rip and other websites
3️⃣ View search results and decide what to download

Usage examples:
- "Search for Frieren"
- "Search for Oshi no Ko on mikan"
- "Download this RSS: https://mikan.moe/RSS/..."

Note: I will respond in the same language you use to communicate with me!

Start chatting with me!"""
    UNAUTHORIZED_MESSAGE = "❌ You are not authorized to use this bot"
    HISTORY_CLEARED_MESSAGE = "✅ Conversation history cleared"

    def __init__(self, download_manager: DownloadManager):
        """Initialize Telegram assistant.

        Args:
            download_manager: DownloadManager instance
        """
        self.download_manager = download_manager
        self.assistant = AniAssistant(download_manager)
        self.bot_token = config.assistant.telegram.bot_token
        self.allowed_users = set(config.assistant.telegram.allowed_users)
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"

        # Shared HTTP session for all API calls
        self.session = None

        # Store conversation history for each user
        self.user_histories: Dict[int, List[dict]] = {}

        # Store status message IDs for each chat
        self.status_messages: Dict[int, int] = {}

        # Offset for long polling
        self.update_offset = 0

        logger.info(
            f"Telegram assistant initialized. Allowed users: {self.allowed_users}"
        )

    async def run(self) -> None:
        """Run the Telegram bot (long polling loop)."""
        logger.info("Starting Telegram assistant...")

        if not self.bot_token:
            logger.error("Telegram bot token not configured")
            return

        async with aiohttp.ClientSession(trust_env=True) as self.session:
            await self._run_polling_loop()

    async def _run_polling_loop(self) -> None:
        if not await self._log_bot_info():
            return

        # Main polling loop
        while True:
            try:
                updates = await self.get_updates()

                for update in updates:
                    try:
                        await self.process_update(update)
                    except Exception as e:
                        logger.exception(f"Error processing update: {e}")

            except Exception as e:
                logger.exception(f"Error in Telegram polling loop: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def process_update(self, update: dict) -> None:
        """Process a single Telegram update.

        Args:
            update: Telegram update object
        """
        self._update_polling_offset(update)

        message_context = self._extract_message_context(update)
        if not message_context:
            return

        chat_id, user_id, text = message_context

        if not await self._authorize_user(chat_id, user_id):
            return

        logger.info(f"Received message from {user_id}: {text}")

        if await self._handle_command(chat_id, user_id, text):
            return

        await self._process_user_message(chat_id, user_id, text)

    @staticmethod
    def _status_to_text(status: AssistantStatus, payload: dict) -> str:
        """Map assistant status event to Telegram-friendly text."""
        if status == AssistantStatus.THINKING:
            return "🤔 正在思考..."

        if status == AssistantStatus.FINALIZING:
            return "✍️ 正在整理回复..."

        if status == AssistantStatus.TOOL_EXECUTING:
            tool_name = payload.get("tool_name", "")
            if tool_name == "download_resource" and payload.get("title"):
                return f"⬇️ 正在下载: {payload['title']}"

            tool_status_messages = {
                "search_anime_resources": "🔍 正在搜索动画资源...",
                "parse_rss": "📡 正在解析 RSS 订阅...",
                "execute_sql_query": "💾 正在查询下载历史数据库...",
            }
            return tool_status_messages.get(tool_name, f"⚙️ 正在执行 {tool_name}...")

        return "⚙️ 正在处理中..."

    async def send_message(self, chat_id: int, text: str) -> int:
        """Send message to Telegram user.

        Args:
            chat_id: Telegram chat ID
            text: Message text

        Returns:
            Message ID if successful, 0 otherwise
        """
        url = f"{self.api_base}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
        }

        try:
            async with self.session.post(url, json=payload, timeout=30) as response:
                response.raise_for_status()
                data = await response.json()
                if data.get("ok"):
                    return data.get("result", {}).get("message_id", 0)
                return 0
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {chat_id}: {e}")
            return 0

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> bool:
        """Edit an existing message.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to edit
            text: New message text

        Returns:
            True if successful
        """
        url = f"{self.api_base}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }

        try:
            async with self.session.post(url, json=payload, timeout=30) as response:
                response.raise_for_status()
                return True
        except Exception as e:
            logger.error(
                f"Failed to edit Telegram message {message_id} in chat {chat_id}: {e}"
            )
            return False

    async def get_updates(self) -> List[dict]:
        """Get updates from Telegram using long polling.

        Returns:
            List of update objects
        """
        url = f"{self.api_base}/getUpdates"
        params = {
            "offset": self.update_offset,
            "timeout": 30,
        }

        try:
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                if data.get("ok"):
                    return data.get("result", [])
                else:
                    logger.error(f"Telegram API error: {data}")
                    return []
        except asyncio.TimeoutError:
            # Timeout is expected with long polling
            return []
        except Exception as e:
            logger.error(f"Failed to get Telegram updates: {e}")
            return []

    async def _delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message.

        Args:
            chat_id: Telegram chat ID
            message_id: Message ID to delete

        Returns:
            True if successful
        """
        url = f"{self.api_base}/deleteMessage"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
        }

        try:
            async with self.session.post(url, json=payload, timeout=5) as response:
                response.raise_for_status()
                return True
        except Exception:
            return False  # Not critical if this fails

    def _update_polling_offset(self, update: dict) -> None:
        """Update long-polling offset from the received update."""
        update_id = update.get("update_id")
        if update_id:
            self.update_offset = max(self.update_offset, update_id + 1)

    @staticmethod
    def _extract_message_context(
        update: dict,
    ) -> Optional[Tuple[int, Optional[int], str]]:
        """Extract chat id, user id and text from update message."""
        message = update.get("message")
        if not message:
            return None

        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
        text = message.get("text")

        if not chat_id or not text:
            return None

        return chat_id, user_id, text

    async def _authorize_user(self, chat_id: int, user_id: Optional[int]) -> bool:
        """Check whether the user is allowed to use this bot."""
        if not self.allowed_users or user_id in self.allowed_users:
            return True

        logger.warning(f"Unauthorized user {user_id} tried to use bot")
        await self.send_message(chat_id, self.UNAUTHORIZED_MESSAGE)
        return False

    async def _handle_command(
        self, chat_id: int, user_id: Optional[int], text: str
    ) -> bool:
        """Handle built-in commands.

        Returns:
            True if command was handled.
        """
        if text == "/start":
            await self.send_message(chat_id, self.WELCOME_MESSAGE)
            return True

        if text == "/clear":
            if user_id is not None:
                self.user_histories.pop(user_id, None)
            await self.send_message(chat_id, self.HISTORY_CLEARED_MESSAGE)
            return True

        return False

    async def _process_user_message(
        self, chat_id: int, user_id: Optional[int], text: str
    ) -> None:
        """Process a normal user message through AniAssistant."""
        history = self._get_or_create_history(user_id)

        try:
            status_callback = self._build_status_callback(chat_id)
            response = await self.assistant.process_message(
                text, history, status_callback
            )

            await self._clear_status_message(chat_id)
            self._append_history(history, text, response)
            await self.send_message(chat_id, response)
        except Exception as e:
            logger.exception(f"Error processing message from {user_id}")
            await self.send_message(chat_id, f"❌ Error processing message: {str(e)}")

    def _get_or_create_history(self, user_id: Optional[int]) -> List[dict]:
        """Get or initialize conversation history for a user."""
        if user_id is None:
            return []
        return self.user_histories.setdefault(user_id, [])

    def _append_history(
        self, history: List[dict], user_text: str, response: str
    ) -> None:
        """Append one user/assistant turn and trim by max history limit."""
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": response})

        max_history = self.assistant.max_history * 2
        if len(history) > max_history:
            history[:] = history[-max_history:]

    def _build_status_callback(
        self, chat_id: int
    ) -> Callable[[AssistantStatus, dict], Awaitable[None]]:
        """Create chat-specific status callback for assistant progress events."""

        async def status_callback(status: AssistantStatus, payload: dict) -> None:
            status_text = self._status_to_text(status, payload)
            await self._upsert_status_message(chat_id, status_text)

        return status_callback

    async def _upsert_status_message(self, chat_id: int, status_text: str) -> None:
        """Create or update status message for a chat."""
        message_id = self.status_messages.get(chat_id)
        if message_id:
            await self.edit_message(chat_id, message_id, status_text)
            return

        new_message_id = await self.send_message(chat_id, status_text)
        if new_message_id:
            self.status_messages[chat_id] = new_message_id

    async def _clear_status_message(self, chat_id: int) -> None:
        """Delete and clear tracked status message for a chat if present."""
        message_id = self.status_messages.pop(chat_id, None)
        if message_id:
            await self._delete_message(chat_id, message_id)

    async def _log_bot_info(self) -> bool:
        """Fetch and log bot profile information."""
        try:
            async with self.session.get(
                f"{self.api_base}/getMe", timeout=10
            ) as response:
                response.raise_for_status()
                data = await response.json()

            if data.get("ok"):
                bot_info = data.get("result", {})
                logger.info(
                    f"Bot started: @{bot_info.get('username')} ({bot_info.get('first_name')})"
                )
            return True
        except Exception as e:
            logger.error(f"Failed to get bot info: {e}")
            return False
