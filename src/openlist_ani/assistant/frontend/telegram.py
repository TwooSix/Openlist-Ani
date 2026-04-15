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
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from telegram import BotCommand, Message as TGMessage, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from openlist_ani.assistant.core.message_queue import PendingMessage
from openlist_ani.assistant.core.models import EventType
from openlist_ani.assistant.frontend.base import Frontend

if TYPE_CHECKING:
    from openlist_ani.assistant.core.loop import AgenticLoop
    from openlist_ani.assistant.skill.catalog import SkillCatalog

# Telegram message length limit
MAX_MESSAGE_LENGTH = 4096

# Minimum interval between editMessageText calls (seconds).
# Telegram Bot API returns 429 if edits are too frequent.
_EDIT_DEBOUNCE_SECONDS = 1.0

# Interval between repeated sendChatAction(TYPING) calls (seconds).
# Telegram typing status expires after ~5 s; resend every 4 s to keep
# the indicator visible until the agent finishes its turn.
_TYPING_INTERVAL_SECONDS = 4.0

# Shared status text constants
_STATUS_THINKING = "\u23f3 Thinking..."

# ── MarkdownV2 escape ──────────────────────────────────────────────
# Characters that must be escaped in MarkdownV2:
# _ * [ ] ( ) ~ ` > # + - = | { } . !
_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_mdv2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text)


def _format_tool_args(args: dict) -> str:
    """Format tool arguments compactly for inline display.

    Mirrors TextualFrontend._format_tool_args.
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

    Session management is per-chat: each ``chat_id`` gets its own
    JSONL session that is automatically resumed across bot restarts.
    """

    def __init__(
        self,
        loop: AgenticLoop,
        bot_token: str,
        allowed_users: list[int] | None = None,
        *,
        loop_factory: Callable[[], AgenticLoop] | None = None,
        catalog: SkillCatalog | None = None,
    ) -> None:
        super().__init__(loop)
        self._bot_token = bot_token
        self._allowed_users = set(allowed_users) if allowed_users else None
        self._app: Application | None = None
        self._catalog = catalog

        # Per-chat loops: each chat_id -> its own AgenticLoop
        # Isolates conversation history across users / groups.
        self._chat_loops: dict[int, AgenticLoop] = {}

        # Factory callable to create new loops (set by __init__.py)
        # Falls back to returning the shared loop if no factory is set.
        self._loop_factory = loop_factory

        # Track active turns per chat_id so concurrent messages
        # are enqueued instead of blocking on the lock.
        self._active_turns: set[int] = set()

    async def _get_loop(self, chat_id: int) -> AgenticLoop:
        """Get or create an AgenticLoop for a given chat_id.

        On first access for a chat_id, creates a new loop and sets up
        its session: resumes the most recent telegram session for this
        chat_id, or starts a new one.
        """
        if chat_id not in self._chat_loops:
            if self._loop_factory is not None:
                loop = self._loop_factory()
            else:
                # Fallback: use the single shared loop (CLI-style)
                loop = self._loop
            self._chat_loops[chat_id] = loop

            # Set up per-chat session
            await self._setup_chat_session(chat_id, loop)

        return self._chat_loops[chat_id]

    async def _setup_chat_session(
        self, chat_id: int, loop: AgenticLoop
    ) -> None:
        """Resume or create a session for a specific chat_id."""
        storage = loop.session_storage
        if storage is None:
            return

        # Find existing telegram sessions for this chat_id
        existing = await storage.list_sessions()
        matching = [
            s for s in existing
            if s.metadata.get("frontend") == "telegram"
            and s.metadata.get("chat_id") == chat_id
        ]

        if matching:
            latest = matching[0]  # sorted by mtime desc
            await loop.resume(latest.session_id)
            logger.info(
                f"Resumed session {latest.session_id} for chat {chat_id}"
            )
        else:
            await storage.start_new_session(
                metadata={
                    "frontend": "telegram",
                    "chat_id": chat_id,
                }
            )
            logger.info(f"Created new session for chat {chat_id}")

    async def run(self) -> None:
        """Start the Telegram bot in polling mode."""
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .concurrent_updates(True)
            .build()
        )

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("clear", self._cmd_clear))
        self._app.add_handler(CommandHandler("dream", self._cmd_dream))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        # Catch-all for unrecognized /commands — handles skill invocations
        self._app.add_handler(
            MessageHandler(filters.COMMAND, self._handle_command_fallback)
        )
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        logger.info("Starting Telegram assistant bot...")
        await self._app.initialize()
        await self._app.start()
        await self._register_commands()
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

    async def _register_commands(self) -> None:
        """Register bot commands with Telegram for the command menu.

        When users type "/" in the chat, Telegram clients display a
        command menu with all registered commands and their descriptions.

        Registers built-in commands plus any skill commands discovered
        by the catalog.
        """
        commands: list[BotCommand] = [
            BotCommand("help", "Show available commands"),
            BotCommand("clear", "Start a new session"),
            BotCommand("dream", "Run memory consolidation"),
        ]

        # Add skill commands from the catalog
        if self._catalog is not None:
            for skill in self._catalog.all_skills():
                # Telegram bot commands must match /^[a-z0-9_]{1,32}$/
                cmd_name = skill.name.lower().replace("-", "_")
                if len(cmd_name) > 32:
                    cmd_name = cmd_name[:32]
                # Telegram limits command descriptions to 256 characters
                desc = skill.description.strip()
                if len(desc) > 256:
                    desc = desc[:253] + "..."
                commands.append(BotCommand(cmd_name, desc))

        try:
            await self._app.bot.set_my_commands(commands)
            cmd_names = [c.command for c in commands]
            logger.info(f"Registered {len(commands)} bot commands: {cmd_names}")
        except Exception as e:
            logger.warning(f"Failed to register bot commands: {e}")

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
        1. Validate the message and authorize the user.
        2. Delegate to _process_user_turn which handles the full
           thinking -> streaming -> result lifecycle.
        """
        if not update.message or not update.message.text:
            return

        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            await update.message.reply_text("Unauthorized.")
            return

        await self._process_user_turn(update, update.message.text)

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
        lines: list[str] = [_STATUS_THINKING]
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
        """Handle a single streaming event from the agentic loop.

        Uses a dispatch table to map event types to handlers, keeping
        each handler simple and the overall method flat.
        """
        # TEXT_DONE is pure data collection — no async I/O needed
        if event.type == EventType.TEXT_DONE:
            if event.text:
                final_parts.append(event.text)
            return

        handlers = {
            EventType.TOOL_START: self._on_tool_start,
            EventType.TOOL_END: self._on_tool_end,
            EventType.TEXT_DELTA: self._on_text_delta,
            EventType.ERROR: self._on_error,
            EventType.USER_MESSAGE_INJECTED: self._on_injected,
        }
        handler = handlers.get(event.type)
        if handler is not None:
            await handler(event, status_msg, lines, edit_state, final_parts)

    async def _on_tool_start(self, event, status_msg, lines, edit_state, _final_parts):
        lines[0] = "⚙️ Working..."
        args_str = _format_tool_args(event.tool_args)
        tool_line = f"🔧 {event.tool_name}"
        if args_str:
            tool_line += f"({args_str})"
        lines.append(tool_line)
        await self._debounced_edit(status_msg, lines, edit_state)

    async def _on_tool_end(self, event, status_msg, lines, edit_state, _final_parts):
        preview = event.tool_result_preview.replace("\n", " ")[:60]
        if preview:
            lines.append(f"  ↳ {preview}")
            await self._debounced_edit(status_msg, lines, edit_state)

    async def _on_text_delta(self, _event, status_msg, lines, edit_state, _final_parts):
        if lines[0] != "✍️ Generating...":
            lines[0] = "✍️ Generating..."
            await self._debounced_edit(status_msg, lines, edit_state)

    async def _on_error(self, event, status_msg, lines, edit_state, _final_parts):
        lines.append(f"❌ {event.text}")
        await self._debounced_edit(status_msg, lines, edit_state)

    async def _on_injected(self, event, status_msg, lines, edit_state, _final_parts):
        preview = event.text[:60] if len(event.text) <= 60 else event.text[:57] + "..."
        lines.append(f"💬 [injected] {preview}")
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

    # ── Typing indicator ─────────────────────────────────────────

    def _start_typing_indicator(self, chat_id: int) -> asyncio.Task:
        """Start a background task that continuously sends TYPING action.

        Telegram's typing indicator expires after ~5 seconds, so this
        task resends it every ``_TYPING_INTERVAL_SECONDS`` until cancelled.

        Args:
            chat_id: The chat to show the typing indicator in.

        Returns:
            The background ``asyncio.Task`` — cancel it to stop.
        """

        async def _keep_typing() -> None:
            try:
                while True:
                    await self._app.bot.send_chat_action(
                        chat_id=chat_id,
                        action=ChatAction.TYPING,
                    )
                    await asyncio.sleep(_TYPING_INTERVAL_SECONDS)
            except Exception as e:  # noqa: BLE001
                if not isinstance(e, asyncio.CancelledError):
                    logger.debug(f"Typing indicator failed (non-fatal): {e}")
                raise

        return asyncio.create_task(_keep_typing())

    @staticmethod
    async def _stop_typing_indicator(task: asyncio.Task) -> None:
        """Cancel the typing indicator background task.

        Args:
            task: The task returned by :meth:`_start_typing_indicator`.
        """
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # ── Command handlers ──────────────────────────────────────────

    async def _handle_command_fallback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle unrecognized /commands — try skill invocation.

        If the command matches a known skill name, inject the skill
        content and process the message. Otherwise, ignore silently.
        """
        if not update.message or not update.message.text:
            return

        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            await update.message.reply_text("Unauthorized.")
            return

        text = update.message.text
        # Parse: "/mikan search frieren" -> cmd_name="mikan", user_msg="search frieren"
        # Telegram may append @botname: "/mikan@mybot search frieren"
        parts = text.strip().split(None, 1)
        cmd_part = parts[0].lstrip("/").split("@")[0].lower() if parts else ""
        user_msg = parts[1] if len(parts) > 1 else ""

        if self._catalog is None:
            return

        # Try exact match first, then try with underscores→hyphens
        # (Telegram commands use underscores, skill names may use hyphens)
        skill = self._catalog.get_skill(cmd_part)
        if skill is None:
            skill = self._catalog.get_skill(cmd_part.replace("_", "-"))
        if skill is None:
            return

        skill_name = skill.name
        skill_content = self._catalog.get_skill_content(skill_name) or ""
        augmented = self._build_skill_message(
            skill_name=skill_name,
            skill_content=skill_content,
            skill_base_dir=str(skill.base_dir),
            user_message=user_msg,
        )
        await self._process_user_turn(update, augmented)

    async def _process_user_turn(
        self,
        update: Update,
        message_text: str,
    ) -> None:
        """Run a full thinking → streaming → result turn for a message.

        Shared by _handle_text and _handle_command_fallback to avoid
        duplicating the turn lifecycle (status message, streaming, cleanup).
        """
        chat_id = update.message.chat_id
        loop = await self._get_loop(chat_id)

        if chat_id in self._active_turns:
            loop.message_queue.enqueue(PendingMessage(content=message_text))
            return

        self._active_turns.add(chat_id)
        status_msg = await update.message.reply_text(_STATUS_THINKING)
        typing_task = self._start_typing_indicator(chat_id)
        try:
            final_parts = await self._stream_events(loop, message_text, status_msg)
            await self._send_final_result(update, status_msg, final_parts)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self._cleanup_status_message(status_msg)
            await update.message.reply_text(f"Error: {e}")
        finally:
            await self._stop_typing_indicator(typing_task)
            self._active_turns.discard(chat_id)

    @staticmethod
    def _build_skill_message(
        skill_name: str,
        skill_content: str,
        skill_base_dir: str,
        user_message: str,
    ) -> str:
        """Build the augmented user message with skill context."""
        parts: list[str] = [
            f"<command-name>/{skill_name}</command-name>",
            f'<skill name="{skill_name}">',
            f"Base directory for this skill: {skill_base_dir}",
            "",
            skill_content,
            "</skill>",
        ]
        if user_message:
            parts.append("")
            parts.append(user_message)
        return "\n".join(parts)

    async def _cmd_start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /start command."""
        lines = [
            "Hello! I'm your AI assistant. Send me a message to get started.\n",
            "Commands:",
            "/help - Show this help",
            "/clear - Start a new session",
            "/dream - Run memory consolidation",
        ]

        # List skill commands
        if self._catalog is not None:
            skills = self._catalog.all_skills()
            if skills:
                lines.append("")
                lines.append("Skills:")
                for skill in skills:
                    desc = skill.description.strip().split("\n")[0]
                    if len(desc) > 80:
                        desc = desc[:77] + "..."
                    cmd_name = skill.name.replace("-", "_")
                    lines.append(f"/{cmd_name} - {desc}")

        await update.message.reply_text("\n".join(lines))

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
        """Handle /clear command — start a new session for this chat."""
        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            return

        chat_id = update.message.chat_id
        loop = await self._get_loop(chat_id)
        loop.reset()

        # Start a new session in session storage
        if loop.session_storage:
            await loop.session_storage.start_new_session(
                metadata={
                    "frontend": "telegram",
                    "chat_id": chat_id,
                }
            )

        await update.message.reply_text("New session started.")

    async def _cmd_dream(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /dream command — manually trigger memory consolidation."""
        user_id = update.message.from_user.id if update.message.from_user else 0
        if not self._is_authorized(user_id):
            return

        chat_id = update.message.chat_id
        loop = await self._get_loop(chat_id)

        if loop.auto_dream_runner is None:
            await update.message.reply_text("Auto-dream is not configured.")
            return

        status = await update.message.reply_text("🧠 Running memory consolidation...")
        try:
            result = await loop.auto_dream_runner.force_run()
            if result and result.files_touched:
                files_str = ", ".join(result.files_touched)
                await status.edit_text(
                    f"✅ Dream complete!\n"
                    f"Sessions reviewed: {result.sessions_reviewed}\n"
                    f"Files updated: {files_str}\n"
                    f"Summary: {result.summary[:200]}"
                )
            else:
                summary = result.summary if result else "No changes needed."
                await status.edit_text(f"💤 {summary}")
        except Exception as e:
            logger.error(f"Dream command failed: {e}")
            await status.edit_text(f"❌ Dream failed: {e}")
