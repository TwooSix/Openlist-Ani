"""
Telegram frontend for the assistant.

Uses python-telegram-bot in polling mode.

Each Telegram chat (user or group) gets its own AgenticLoop instance
so that conversation histories are fully isolated.

Interaction flow (mirrors CLI frontend):
1. User sends a message.
2. Thinking phase: bot sends a temporary status message and dynamically
   edits it to show spinner + tool execution progress.
3. Result phase: delete the temporary message, send the final AI
   response as a new message (MarkdownV2 with plain-text fallback).
"""

from __future__ import annotations

import asyncio
from loguru import logger
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from telegram import Message as TGMessage, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from openlist_ani.assistant.core.models import EventType
from openlist_ani.assistant.frontend.base import Frontend

if TYPE_CHECKING:
    from openlist_ani.assistant.core.loop import AgenticLoop

# Telegram message length limit
MAX_MESSAGE_LENGTH = 4096

# Minimum interval between editMessageText calls (seconds).
# Telegram Bot API returns 429 if edits are too frequent.
_EDIT_DEBOUNCE_SECONDS = 1.0

# ── MarkdownV2 escape ──────────────────────────────────────────────
# Characters that must be escaped in MarkdownV2:
# _ * [ ] ( ) ~ ` > # + - = | { } . !
_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_mdv2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text)


def _format_tool_args(args: dict) -> str:
    """Format tool arguments compactly for inline display.

    Mirrors CLIFrontend._format_tool_args.
    """
    if not args:
        return ""
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, str):
            val_str = value if len(value) <= 30 else value[:27] + "..."
            parts.append(f'{key}="{val_str}"')
        elif isinstance(value, dict):
            parts.append(f"{key}={{...}}")
        elif isinstance(value, list):
            parts.append(f"{key}=[...]")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


class TelegramFrontend(Frontend):
    """Telegram bot frontend for the assistant.

    Maintains a per-chat AgenticLoop so that each conversation is
    isolated (different users / groups never share message history).
    """

    def __init__(
        self,
        loop: AgenticLoop,
        bot_token: str,
        allowed_users: list[int] | None = None,
        *,
        loop_factory: LoopFactory | None = None,
    ) -> None:
        super().__init__(loop)
        self._bot_token = bot_token
        self._allowed_users = set(allowed_users) if allowed_users else None
        self._app: Application | None = None

        # Per-chat loops: each chat_id -> its own AgenticLoop
        # Isolates conversation history across users / groups.
        self._chat_loops: dict[int, AgenticLoop] = {}

        # Factory callable to create new loops (set by __init__.py)
        # Falls back to returning the shared loop if no factory is set.
        self._loop_factory = loop_factory

    def _get_loop(self, chat_id: int) -> AgenticLoop:
        """Get or create an AgenticLoop for a given chat_id."""
        if chat_id not in self._chat_loops:
            if self._loop_factory is not None:
                self._chat_loops[chat_id] = self._loop_factory()
            else:
                # Fallback: use the single shared loop (CLI-style)
                self._chat_loops[chat_id] = self._loop
        return self._chat_loops[chat_id]

    async def run(self) -> None:
        """Start the Telegram bot in polling mode."""
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .build()
        )

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("clear", self._cmd_clear))
        self._app.add_handler(CommandHandler("reset", self._cmd_reset))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        logger.info("Starting Telegram assistant bot...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_response(self, text: str) -> None:
        """Not used directly — responses go through _handle_text."""
        pass

    def _is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized to use the bot."""
        if self._allowed_users is None:
            return True
        return user_id in self._allowed_users

    # ── Debounced message editing ─────────────────────────────────

    async def _debounced_edit(
        self,
        msg: TGMessage,
        lines: list[str],
        state: dict,
    ) -> None:
        """Edit the status message with debounce to avoid Telegram 429.

        Args:
            msg: The Telegram message to edit.
            lines: Current status lines to join and display.
            state: Mutable dict holding ``last_edit_time`` and ``pending``.
        """
        now = time.monotonic()
        elapsed = now - state["last_edit_time"]
        text = "\n".join(lines)

        if elapsed >= _EDIT_DEBOUNCE_SECONDS:
            await self._safe_edit(msg, text)
            state["last_edit_time"] = time.monotonic()
            state["pending"] = False
        else:
            # Mark as pending — will be flushed later
            state["pending"] = True
            state["pending_text"] = text

    async def _flush_pending_edit(
        self,
        msg: TGMessage,
        state: dict,
    ) -> None:
        """Flush any pending edit that was deferred by debounce."""
        if state.get("pending") and state.get("pending_text"):
            await self._safe_edit(msg, state["pending_text"])
            state["pending"] = False
            state["last_edit_time"] = time.monotonic()

    @staticmethod
    async def _safe_edit(msg: TGMessage, text: str) -> None:
        """Edit a message, silently ignoring errors (rate-limit, etc.)."""
        try:
            await msg.edit_text(text)
        except Exception as e:
            # Telegram may throw BadRequest if text hasn't changed,
            # or Flood control — either way, non-fatal.
            logger.debug(f"edit_text failed (non-fatal): {e}")

    # ── Send final result ─────────────────────────────────────────

    async def _send_chunked(
        self,
        update: Update,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> None:
        """Send a long message in chunks to respect Telegram limits.

        If *parse_mode* is set and sending fails (e.g. bad MarkdownV2),
        automatically falls back to plain text.
        """
        if not text:
            return

        chunks = self._split_text(text)

        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode=parse_mode)
            except Exception:
                if parse_mode is not None:
                    # Fallback: send as plain text
                    logger.warning(
                        "Failed to send with parse_mode=%s, falling back to plain text",
                        parse_mode,
                    )
                    try:
                        await update.message.reply_text(chunk)
                    except Exception as e2:
                        logger.error(f"Failed to send message chunk: {e2}")
                else:
                    logger.error("Failed to send message chunk")

    @staticmethod
    def _split_text(text: str) -> list[str]:
        """Split text into chunks that fit within Telegram limits."""
        chunks: list[str] = []
        while text:
            if len(text) <= MAX_MESSAGE_LENGTH:
                chunks.append(text)
                break
            # Find a good break point
            cut = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
            if cut == -1:
                cut = MAX_MESSAGE_LENGTH
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        return chunks

    # ── Main message handler ──────────────────────────────────────

    async def _handle_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle incoming text messages with real-time progress display.

        Flow:
        1. Send a temporary "⏳ Thinking..." message.
        2. As events stream in, edit the message to show tool activity.
        3. Delete the temporary status message, send the final result.
        """
        if not update.message or not update.message.text:
            return

        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            await update.message.reply_text("Unauthorized.")
            return

        chat_id = update.message.chat_id
        user_text = update.message.text

        # Get per-chat loop (creates one if needed)
        loop = self._get_loop(chat_id)

        # Send the temporary status message
        status_msg = await update.message.reply_text("⏳ Thinking...")

        try:
            final_parts = await self._stream_events(loop, user_text, status_msg)
            await self._send_final_result(update, status_msg, final_parts)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self._cleanup_status_message(status_msg)
            await update.message.reply_text(f"Error: {e}")

    async def _stream_events(
        self,
        loop: AgenticLoop,
        user_text: str,
        status_msg: TGMessage,
    ) -> list[str]:
        """Process all streaming events from the agentic loop.

        Returns:
            List of final text parts collected from TEXT_DONE events.
        """
        lines: list[str] = ["⏳ Thinking..."]
        edit_state: dict = {
            "last_edit_time": 0.0,
            "pending": False,
            "pending_text": "",
        }
        final_parts: list[str] = []

        async for event in loop.process(user_text):
            await self._handle_stream_event(
                event, status_msg, lines, edit_state, final_parts,
            )

        # Flush any pending edit before returning
        await self._flush_pending_edit(status_msg, edit_state)
        return final_parts

    async def _handle_stream_event(
        self,
        event: object,
        status_msg: TGMessage,
        lines: list[str],
        edit_state: dict,
        final_parts: list[str],
    ) -> None:
        """Handle a single streaming event from the agentic loop."""
        if event.type == EventType.THINKING:
            return  # Thinking events are intentionally not displayed

        elif event.type == EventType.TOOL_START:
            lines[0] = "⚙️ Working..."
            args_str = _format_tool_args(event.tool_args)
            tool_line = f"🔧 {event.tool_name}"
            if args_str:
                tool_line += f"({args_str})"
            lines.append(tool_line)
            await self._debounced_edit(status_msg, lines, edit_state)

        elif event.type == EventType.TOOL_END:
            preview = event.tool_result_preview.replace("\n", " ")[:60]
            if preview:
                lines.append(f"  ↳ {preview}")
                await self._debounced_edit(status_msg, lines, edit_state)

        elif event.type == EventType.TEXT_DELTA:
            if lines[0] != "✍️ Generating...":
                lines[0] = "✍️ Generating..."
                await self._debounced_edit(status_msg, lines, edit_state)

        elif event.type == EventType.TEXT_DONE:
            if event.text:
                final_parts.append(event.text)

        elif event.type == EventType.ERROR:
            lines.append(f"❌ {event.text}")
            await self._debounced_edit(status_msg, lines, edit_state)

    async def _send_final_result(
        self,
        update: Update,
        status_msg: TGMessage,
        final_parts: list[str],
    ) -> None:
        """Delete the status message and send the final result."""
        await self._cleanup_status_message(status_msg)

        full_response = "\n".join(final_parts)
        if full_response:
            escaped = _escape_mdv2(full_response)
            await self._send_chunked(
                update, escaped, parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.reply_text("No response.")

    @staticmethod
    async def _cleanup_status_message(status_msg: TGMessage) -> None:
        """Delete the temporary status message, ignoring errors."""
        try:
            await status_msg.delete()
        except Exception as e:
            logger.debug(f"Failed to delete status message: {e}")

    # ── Command handlers ──────────────────────────────────────────

    async def _cmd_start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /start command."""
        await update.message.reply_text(
            "Hello! I'm your AI assistant. Send me a message to get started.\n\n"
            "Commands:\n"
            "/help - Show this help\n"
            "/clear - Clear session history\n"
            "/reset - Reset all memory"
        )

    async def _cmd_help(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /help command."""
        await self._cmd_start(update, context)

    async def _cmd_clear(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /clear command — clear session history for this chat."""
        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            return

        chat_id = update.message.chat_id
        loop = self._get_loop(chat_id)
        loop.reset()
        await update.message.reply_text("Session history cleared.")

    async def _cmd_reset(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /reset command — clear all memory for this chat."""
        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            return

        chat_id = update.message.chat_id
        loop = self._get_loop(chat_id)
        loop.reset()

        # Also clear persistent memory
        memory = loop._memory
        await memory.clear_all()
        await update.message.reply_text("All session history has been reset.")


# Type alias for the loop factory callable
LoopFactory = Callable[[], "AgenticLoop"]
