"""
Telegram bot integration for assistant.
"""

import asyncio
from typing import Awaitable, Callable

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..backend.client import BackendClient
from ..config import config
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

    def __init__(self, backend_client: BackendClient):
        """Initialize Telegram assistant.

        Args:
            backend_client: BackendClient instance for backend API interaction
        """
        self.backend_client = backend_client
        self.assistant = AniAssistant(backend_client)
        self.bot_token = config.assistant.telegram.bot_token
        self.allowed_users = set(config.assistant.telegram.allowed_users)
        self.application: Application | None = None

        # Store status message IDs for each chat
        self.status_messages: dict[int, int] = {}

        logger.info(
            f"Telegram assistant initialized. Allowed users: {self.allowed_users}"
        )

    async def run(self) -> None:
        """Run the Telegram bot with python-telegram-bot polling."""
        logger.info("Starting Telegram assistant...")

        if not self.bot_token:
            logger.error("Telegram bot token not configured")
            return

        self.application = ApplicationBuilder().token(self.bot_token).build()
        self._register_handlers(self.application)

        try:
            await self.application.initialize()
            await self.application.bot.delete_webhook(drop_pending_updates=False)

            while True:
                try:
                    await self._start_polling()
                    await asyncio.Future()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(f"Error in Telegram polling loop: {exc}")
                finally:
                    await self._stop_polling()
                await asyncio.sleep(5)
        finally:
            await self._shutdown_application()

    def _register_handlers(self, application: Application) -> None:
        """Register Telegram command and message handlers."""
        application.add_handler(CommandHandler("start", self._handle_start_command))
        application.add_handler(CommandHandler("clear", self._handle_clear_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text_message)
        )

    async def _start_polling(self) -> None:
        """Start the application and begin polling for Telegram updates."""
        if self.application is None:
            raise RuntimeError("Telegram application is not initialized")

        await self.application.start()

        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")

        await self.application.updater.start_polling()

        bot_info = await self.application.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username} ({bot_info.first_name})")

    async def _stop_polling(self) -> None:
        """Stop polling and the application, keeping it ready for restart."""
        if self.application is None:
            return

        try:
            if self.application.updater is not None:
                await self.application.updater.stop()
        except Exception:
            logger.debug("Telegram updater stop failed or already stopped")

        try:
            await self.application.stop()
        except Exception:
            logger.debug("Telegram application stop failed or already stopped")

    async def _shutdown_application(self) -> None:
        """Release all application resources on final exit."""
        application = self.application
        self.application = None

        if application is None:
            return

        try:
            if application.updater is not None:
                await application.updater.stop()
        except Exception:
            logger.debug("Telegram updater stop failed or already stopped")

        try:
            await application.stop()
        except Exception:
            logger.debug("Telegram application stop failed or already stopped")

        try:
            await application.shutdown()
        except Exception:
            logger.debug("Telegram application shutdown failed or already shut down")

    @staticmethod
    def _format_status_text(status: AssistantStatus, payload: dict) -> str:
        """Map assistant status event to Telegram-friendly text."""
        if status == AssistantStatus.THINKING:
            return "🤔 正在思考..."

        if status == AssistantStatus.FINALIZING:
            return "✍️ 正在整理回复..."

        if status == AssistantStatus.TOOL_EXECUTING:
            tool_name = payload.get("tool_name", "")
            if tool_name == "download_resource" and payload.get("title"):
                return f"⬇️ 正在下载: {payload['title']}"

            if tool_name == "search_anime_resources":
                website = payload.get("website")
                website_labels = {
                    "mikan": "🍊 正在搜索 Mikan 资源...",
                    "dmhy": "🌸 正在搜索动漫花园资源...",
                    "acgrip": "🛰️ 正在搜索 ACG.RIP 资源...",
                }
                return website_labels.get(website, "🔍 正在搜索动画资源...")

            tool_status_messages = {
                "parse_rss": "📡 正在解析 RSS 订阅...",
                "execute_sql_query": "💾 正在查询下载历史数据库...",
                "get_bangumi_calendar": "📅 正在获取 Bangumi 每日放送...",
                "get_bangumi_subject": "📖 正在查询 Bangumi 番剧详情...",
                "get_bangumi_collection": "📚 正在获取 Bangumi 用户收藏...",
                "get_bangumi_reviews": "💬 正在获取 Bangumi 番剧评论...",
                "update_bangumi_collection": "✏️ 正在更新 Bangumi 收藏状态...",
                "recommend_anime": "🎯 正在分析用户喜好...",
                "mikan_search_bangumi": "🍊 正在搜索 Mikan 番剧条目...",
                "mikan_subscribe_bangumi": "🍊 正在订阅 Mikan 番剧...",
                "mikan_unsubscribe_bangumi": "🍊 正在取消 Mikan 订阅...",
            }
            return tool_status_messages.get(tool_name, f"⚙️ 正在执行 {tool_name}...")

        return "⚙️ 正在处理中..."

    async def _authorize_user(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> bool:
        """Check whether the current Telegram user is authorized."""
        user = update.effective_user
        if not self.allowed_users or (user and user.id in self.allowed_users):
            return True

        logger.warning(
            f"Unauthorized user {getattr(user, 'id', None)} tried to use bot"
        )
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=self.UNAUTHORIZED_MESSAGE,
            )
        return False

    async def _handle_start_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle the /start command."""
        if not await self._authorize_user(update, context):
            return
        if update.effective_message:
            await update.effective_message.reply_text(self.WELCOME_MESSAGE)

    async def _handle_clear_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle the /clear command and wipe persisted memory."""
        if not await self._authorize_user(update, context):
            return

        memory_key = self._build_memory_key(update.effective_user)
        await self.assistant.clear_memory(memory_key)

        if update.effective_chat:
            await self._clear_status_message(context, update.effective_chat.id)
        if update.effective_message:
            await update.effective_message.reply_text(self.HISTORY_CLEARED_MESSAGE)

    async def _handle_text_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle a plain text message."""
        if not await self._authorize_user(update, context):
            return

        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None or not message.text:
            return

        logger.info(
            f"Received message from {getattr(user, 'id', None)}: {message.text}"
        )
        status_callback = self._build_status_callback(context, chat.id)

        try:
            response = await self.assistant.process_message(
                message.text,
                status_callback=status_callback,
                memory_key=self._build_memory_key(user),
            )
            await self._clear_status_message(context, chat.id)
            await message.reply_text(response)
        except Exception as exc:
            logger.exception(
                f"Error processing message from {getattr(user, 'id', None)}"
            )
            await self._clear_status_message(context, chat.id)
            await message.reply_text(f"❌ Error processing message: {str(exc)}")

    @staticmethod
    def _build_memory_key(user) -> str | None:
        """Build a stable persisted memory key for one Telegram user."""
        if user is None:
            return None
        return f"telegram:{user.id}"

    def _build_status_callback(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
    ) -> Callable[[AssistantStatus, dict], Awaitable[None]]:
        """Create chat-specific status callback for assistant progress events."""

        async def status_callback(status: AssistantStatus, payload: dict) -> None:
            status_text = self._format_status_text(status, payload)
            await self._upsert_status_message(context, chat_id, status_text)

        return status_callback

    async def _upsert_status_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        status_text: str,
    ) -> None:
        """Create or update status message for a chat."""
        message_id = self.status_messages.get(chat_id)
        if message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=status_text,
                )
                return
            except BadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
            except TelegramError:
                logger.debug("Failed to edit status message, sending a new one")

        sent_message = await context.bot.send_message(chat_id=chat_id, text=status_text)
        self.status_messages[chat_id] = sent_message.message_id

    async def _clear_status_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
    ) -> None:
        """Delete and clear tracked status message for a chat if present."""
        message_id = self.status_messages.pop(chat_id, None)
        if message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramError:
                logger.debug("Failed to delete status message for chat {}", chat_id)
