"""
Telegram bot integration for assistant.

Uses AI-driven streaming: the LLM decides when to send progress
messages via the built-in ``send_message`` tool. The integration layer
delivers those messages to the Telegram chat and sends a typing
indicator to keep the user informed.
"""

import asyncio
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
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
from .assistant import AniAssistant, StreamCallback

# Telegram message length limit
_MAX_MESSAGE_LENGTH = 4096
# Min interval between stream messages (seconds)
_STREAM_MIN_INTERVAL = 0.5


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
    MEMORY_CLEARED_MESSAGE = "✅ Memory cleared"
    NEW_SESSION_MESSAGE = "✅ New session started"

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
        application.add_handler(CommandHandler("reset", self._handle_reset_command))
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_text_message,
            )
        )

    async def _start_polling(self) -> None:
        """Start the application and begin polling."""
        if self.application is None:
            raise RuntimeError("Telegram application is not initialized")

        await self.application.start()

        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")

        await self.application.updater.start_polling()

        bot_info = await self.application.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username} ({bot_info.first_name})")

    async def _stop_polling(self) -> None:
        """Stop polling and the application."""
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

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

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
        """Handle the /clear command and wipe all persisted memory."""
        if not await self._authorize_user(update, context):
            return

        await self.assistant.clear_memory()

        if update.effective_message:
            await update.effective_message.reply_text(self.MEMORY_CLEARED_MESSAGE)

    async def _handle_reset_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle the /reset command to start a new session."""
        if not await self._authorize_user(update, context):
            return

        await self.assistant.start_new_session()

        if update.effective_message:
            await update.effective_message.reply_text(self.NEW_SESSION_MESSAGE)

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

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

        # Send initial typing indicator
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

        stream_callback = self._build_stream_callback(context, chat.id)

        try:
            response = await self.assistant.process_message(
                message.text,
                stream_callback=stream_callback,
            )
            await self._send_chunked_message(context, chat.id, response)
        except Exception as exc:
            logger.exception(
                f"Error processing message from {getattr(user, 'id', None)}"
            )
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"❌ Error processing message: {str(exc)}",
            )

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------

    def _build_stream_callback(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
    ) -> StreamCallback:
        """Create a stream callback that sends messages and typing."""
        last_sent: list[float] = [0.0]

        async def _stream_cb(text: str) -> None:
            now = time.monotonic()
            elapsed = now - last_sent[0]
            if elapsed < _STREAM_MIN_INTERVAL:
                await asyncio.sleep(_STREAM_MIN_INTERVAL - elapsed)

            await self._send_chunked_message(context, chat_id, text)
            last_sent[0] = time.monotonic()

            # Re-send typing indicator after the message
            try:
                await context.bot.send_chat_action(
                    chat_id=chat_id, action=ChatAction.TYPING
                )
            except TelegramError:
                pass

        return _stream_cb

    @staticmethod
    async def _send_chunked_message(
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
    ) -> None:
        """Send a message, splitting on newline boundaries if too long.

        Args:
            context: Telegram bot context.
            chat_id: Chat to send to.
            text: Full message text.
        """
        if not text:
            return

        if len(text) <= _MAX_MESSAGE_LENGTH:
            await context.bot.send_message(chat_id=chat_id, text=text)
            return

        # Split into chunks on newline boundaries
        remaining = text
        while remaining:
            if len(remaining) <= _MAX_MESSAGE_LENGTH:
                await context.bot.send_message(chat_id=chat_id, text=remaining)
                break

            # Find the last newline within the limit
            split_at = remaining.rfind("\n", 0, _MAX_MESSAGE_LENGTH)
            if split_at <= 0:
                # No good newline boundary — hard-cut
                split_at = _MAX_MESSAGE_LENGTH

            chunk = remaining[:split_at]
            remaining = remaining[split_at:].lstrip("\n")
            await context.bot.send_message(chat_id=chat_id, text=chunk)
