"""
Textual-based TUI frontend for the assistant.

Replaces the old prompt_toolkit + Rich CLIFrontend with a full
Textual application featuring a scrollable conversation area and
a fixed bottom input bar, styled after Claude Code.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from loguru import logger
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.events import DescendantBlur
from textual.screen import ModalScreen
from textual.widgets import Static

from openlist_ani.assistant.core.cancellation import CancellationToken
from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.core.message_queue import PendingMessage
from openlist_ani.assistant.core.models import EventType, LoopEvent
from openlist_ani.assistant.frontend.textual_app.events import LoopEventMessage
from openlist_ani.assistant.frontend.textual_app.styles import (
    APP_CSS,
    STATUS_SUCCESS,
    SUGGESTION,
    TEXT_DIM,
    TEXT_DIM_LIGHT,
)
from openlist_ani.assistant.frontend.textual_app.widgets import (
    InputBox,
    InputSubmitted,
    MessageBlock,
    StreamingBlock,
    ThinkingSpinner,
    WelcomeBanner,
)

if TYPE_CHECKING:
    from openlist_ani.assistant.session.models import SessionInfo
    from openlist_ani.assistant.skill.catalog import SkillCatalog


# ── Helpers ──


def _format_relative_time(mtime: float) -> str:
    """Convert a Unix timestamp to a human-readable relative time string."""
    delta = time.time() - mtime
    if delta < 60:
        return "just now"
    if delta < 3600:
        minutes = int(delta / 60)
        return f"{minutes}m ago"
    if delta < 86400:
        hours = int(delta / 3600)
        return f"{hours}h ago"
    days = int(delta / 86400)
    if days == 1:
        return "1d ago"
    if days < 30:
        return f"{days}d ago"
    return f"{days // 30}mo ago"


def _clean_prompt_text(raw: str) -> str:
    """Clean and truncate prompt text for display in the session picker.

    Strips skill injection wrappers (<command-name>…</skill> prefix),
    collapses whitespace, and truncates to a single-line display.
    """
    import re

    text = raw.strip() if raw else ""
    if not text:
        return "(empty session)"

    # Strip skill injection wrapper:
    #   <command-name>/foo</command-name>\n<skill ...>...</skill>\n\nACTUAL_MSG
    # Pattern: everything up to </skill> + optional trailing newlines
    skill_end = text.find(_SKILL_END_TAG)
    if skill_end != -1:
        text = text[skill_end + len(_SKILL_END_TAG) :].strip()
    elif text.startswith("<command-name>"):
        # The first_prompt was truncated at 100 chars, so </skill> is not
        # present. Extract the command name for display instead.
        cmd_match = re.search(
            r"<command-name>(/\w+)</command-name>",
            text,
        )
        if cmd_match:
            text = cmd_match.group(1)
        else:
            text = "(skill command)"

    # If still empty after stripping, use original
    if not text:
        text = raw.strip()
        # Try to at least strip the command-name tag
        text = re.sub(r"<command-name>.*?</command-name>\s*", "", text)
        text = re.sub(
            r"<skill[^>]*>.*?</skill>\s*",
            "",
            text,
            flags=re.DOTALL,
        )
        text = text.strip() or "(empty session)"

    # Collapse newlines and whitespace to single space
    text = " ".join(text.split())

    # Truncate
    if len(text) > 72:
        text = text[:69] + "\u2026"

    return text


# ── Literal constants (avoid duplication — SonarCloud S1192) ──
_SKILL_END_TAG = "</skill>"
_RESUME_SESSION_LABEL = "Resume Session"

# ── Slash commands ──
_CMD_QUIT = "/quit"
_BUILTIN_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show available commands"),
    ("/clear", "Start a new session"),
    ("/resume", "Resume a previous session"),
    ("/compact", "Compact conversation context"),
    ("/dream", "Run memory consolidation"),
    (_CMD_QUIT, "Exit the assistant"),
]

# CSS selector constants
_CHAT_VIEW = "#chat-view"


# ── Session Picker ──

# CSS for the standalone picker app (Claude Code-inspired)
_PICKER_APP_CSS = """
Screen {
    background: $background;
}
#picker-divider {
    height: 1;
    color: #444444;
    padding: 0 0;
}
#picker-header {
    height: auto;
    min-height: 1;
    padding: 0 1;
    margin-bottom: 1;
}
#picker-list {
    height: 1fr;
    padding: 0 0;
    overflow-y: auto;
    scrollbar-size: 1 1;
    scrollbar-color: #444444;
    scrollbar-color-hover: #b1b9f9;
}
.session-item {
    height: auto;
    padding: 0 0;
    margin: 0 0;
}
.session-item--focused .session-title {
    color: #b1b9f9;
}
.session-item--focused .session-meta {
    color: #b1b9f9;
}
.session-title {
    height: 1;
}
.session-meta {
    height: 1;
    color: #888888;
}
.session-gap {
    height: 1;
}
#picker-footer {
    height: 1;
    dock: bottom;
    padding: 0 1;
    color: #555555;
}
"""


class _SessionListItem(Static):
    """A single session item in the picker list."""

    def __init__(
        self,
        session_id: str,
        title: str,
        metadata_line: str,
        focused: bool = False,
    ) -> None:
        super().__init__()
        self._session_id = session_id
        self._title = title
        self._metadata_line = metadata_line
        self._focused = focused

    def set_focused(self, focused: bool) -> None:
        """Update focus state and refresh the widget in-place."""
        if self._focused != focused:
            self._focused = focused
            self.refresh()

    def render(self) -> Text:
        result = Text()
        if self._focused:
            pointer = "\u276f"  # ❯
            result.append(f"{pointer} ", style=f"bold {SUGGESTION}")
            result.append(f"{self._title}\n", style=f"bold {SUGGESTION}")
            result.append(f"  {self._metadata_line}\n", style=SUGGESTION)
        else:
            result.append(f"  {self._title}\n", style="bold")
            result.append(f"  {self._metadata_line}\n", style=TEXT_DIM_LIGHT)
        return result


class SessionPickerApp(App):
    """Standalone lightweight Textual app for --resume session selection.

    Claude Code-inspired design with ❯ pointer, suggestion-colored
    focused item, and clean two-line layout per session.
    """

    CSS = _PICKER_APP_CSS

    BINDINGS = [
        ("escape", "cancel", "New session"),
        ("q", "cancel", "New session"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("k", "move_up", "Up"),
        ("j", "move_down", "Down"),
        ("enter", "select_session", "Select"),
    ]

    def __init__(self, sessions: list[SessionInfo]) -> None:
        super().__init__()
        self._sessions = sessions
        self.selected_session_id: str | None = None
        self._focused_index: int = 0

    def compose(self) -> ComposeResult:
        count = len(self._sessions)
        divider_text = "\u2500" * 60
        yield Static(
            Text(divider_text, style=TEXT_DIM),
            id="picker-divider",
        )
        header = Text()
        header.append(_RESUME_SESSION_LABEL, style=f"bold {SUGGESTION}")
        if count > 1:
            header.append(
                f" (1 of {count})",
                style=TEXT_DIM_LIGHT,
            )
        yield Static(header, id="picker-header")
        yield Container(id="picker-list")
        footer = Text()
        footer.append("enter", style=f"bold {TEXT_DIM_LIGHT}")
        footer.append(" select  ", style=TEXT_DIM)
        footer.append("esc", style=f"bold {TEXT_DIM_LIGHT}")
        footer.append(" new session  ", style=TEXT_DIM)
        footer.append("\u2191\u2193", style=f"bold {TEXT_DIM_LIGHT}")
        footer.append(" navigate", style=TEXT_DIM)
        yield Static(footer, id="picker-footer")

    async def on_mount(self) -> None:
        await self._render_list()

    async def _render_list(self) -> None:
        """Render the session list once on mount."""
        container = self.query_one("#picker-list", Container)
        await container.remove_children()

        for i, info in enumerate(self._sessions):
            prompt_text = _clean_prompt_text(info.first_prompt)

            metadata_parts = [_format_relative_time(info.mtime)]
            if info.message_count > 0:
                noun = "message" if info.message_count == 1 else "messages"
                metadata_parts.append(f"{info.message_count} {noun}")
            frontend = info.metadata.get("frontend", "")
            if frontend:
                metadata_parts.append(frontend)
            metadata_str = " \u00b7 ".join(metadata_parts)

            item = _SessionListItem(
                session_id=info.session_id,
                title=prompt_text,
                metadata_line=metadata_str,
                focused=(i == self._focused_index),
            )
            await container.mount(item)

        self._update_header_counter()

    def _update_header_counter(self) -> None:
        """Update the header counter text to reflect current focus."""
        count = len(self._sessions)
        if count > 1:
            header = Text()
            header.append(_RESUME_SESSION_LABEL, style=f"bold {SUGGESTION}")
            header.append(
                f" ({self._focused_index + 1} of {count})",
                style=TEXT_DIM_LIGHT,
            )
            self.query_one("#picker-header", Static).update(header)

    def action_move_up(self) -> None:
        if self._focused_index > 0:
            self._move_focus(self._focused_index - 1)

    def action_move_down(self) -> None:
        if self._focused_index < len(self._sessions) - 1:
            self._move_focus(self._focused_index + 1)

    def _move_focus(self, new_index: int) -> None:
        """Move focus from current index to *new_index* without rebuilding."""
        container = self.query_one("#picker-list", Container)
        children = list(container.children)

        old_index = self._focused_index
        self._focused_index = new_index

        # Update only the two affected items in-place
        if 0 <= old_index < len(children):
            old_item = children[old_index]
            if isinstance(old_item, _SessionListItem):
                old_item.set_focused(False)
        if 0 <= new_index < len(children):
            new_item = children[new_index]
            if isinstance(new_item, _SessionListItem):
                new_item.set_focused(True)
                new_item.scroll_visible()

        # Update header counter
        self._update_header_counter()

    def action_select_session(self) -> None:
        if self._sessions:
            self.selected_session_id = self._sessions[self._focused_index].session_id
            self.exit()

    def action_cancel(self) -> None:
        self.selected_session_id = None
        self.exit()


class _SessionPickResult:
    """Container for session picker result, used with asyncio.Event."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.event = asyncio.Event()


class SessionPickerScreen(ModalScreen[str | None]):
    """Modal screen for selecting a session to resume.

    Claude Code-inspired design matching SessionPickerApp.
    Dismisses with the selected session_id, or None if cancelled.
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("up", "move_up", "Up"),
        ("down", "move_down", "Down"),
        ("k", "move_up", "Up"),
        ("j", "move_down", "Down"),
        ("enter", "select_session", "Select"),
    ]

    def __init__(self, sessions: list[SessionInfo]) -> None:
        super().__init__()
        self._sessions = sessions
        self._focused_index: int = 0
        self._pending_tasks: set[asyncio.Task] = set()  # prevent GC

    def compose(self) -> ComposeResult:
        with Vertical(id="session-picker-container"):
            count = len(self._sessions)
            header = Text()
            header.append(_RESUME_SESSION_LABEL, style=f"bold {SUGGESTION}")
            if count > 1:
                header.append(
                    f" (1 of {count})",
                    style=TEXT_DIM_LIGHT,
                )
            yield Static(header, id="session-picker-header")
            yield VerticalScroll(id="session-picker-list")
            footer = Text()
            footer.append("enter", style=f"bold {TEXT_DIM_LIGHT}")
            footer.append(" select  ", style=TEXT_DIM)
            footer.append("esc", style=f"bold {TEXT_DIM_LIGHT}")
            footer.append(" cancel", style=TEXT_DIM)
            yield Static(footer, id="session-picker-footer")

    async def on_mount(self) -> None:
        """Populate the session list."""
        await self._render_list()

    async def _render_list(self) -> None:
        container = self.query_one(
            "#session-picker-list",
            VerticalScroll,
        )
        await container.remove_children()

        for i, info in enumerate(self._sessions):
            prompt_text = _clean_prompt_text(info.first_prompt)

            metadata_parts = [_format_relative_time(info.mtime)]
            if info.message_count > 0:
                noun = "message" if info.message_count == 1 else "messages"
                metadata_parts.append(f"{info.message_count} {noun}")
            frontend = info.metadata.get("frontend", "")
            if frontend:
                metadata_parts.append(frontend)
            metadata_str = " \u00b7 ".join(metadata_parts)

            item = _SessionListItem(
                session_id=info.session_id,
                title=prompt_text,
                metadata_line=metadata_str,
                focused=(i == self._focused_index),
            )
            await container.mount(item)

        # Update header
        count = len(self._sessions)
        if count > 1:
            header = Text()
            header.append(_RESUME_SESSION_LABEL, style=f"bold {SUGGESTION}")
            header.append(
                f" ({self._focused_index + 1} of {count})",
                style=TEXT_DIM_LIGHT,
            )
            self.query_one("#session-picker-header", Static).update(header)

    def _schedule_render(self) -> None:
        """Schedule an async re-render, preventing task GC."""
        task = asyncio.ensure_future(self._render_list())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def action_move_up(self) -> None:
        if self._focused_index > 0:
            self._focused_index -= 1
            self._schedule_render()

    def action_move_down(self) -> None:
        if self._focused_index < len(self._sessions) - 1:
            self._focused_index += 1
            self._schedule_render()

    def action_select_session(self) -> None:
        """Handle Enter — dismiss with the selected session_id."""
        if self._sessions:
            self.dismiss(
                self._sessions[self._focused_index].session_id,
            )

    def action_cancel(self) -> None:
        """Handle Escape — dismiss with None (start fresh session)."""
        self.dismiss(None)


class TextualFrontend(App):
    """Interactive TUI frontend powered by Textual.

    Implements the same interface as Frontend (run / send_response)
    but inherits only from Textual's App to avoid metaclass conflicts
    with ABC.

    Layout:
    - Scrollable #chat-view fills the screen
    - Fixed InputBox docked to the bottom
    """

    CSS = APP_CSS

    BINDINGS = [
        ("ctrl+d", "quit_app", "Exit"),
        ("ctrl+c", "cancel_turn", "Cancel"),
        ("escape", "cancel_turn", "Cancel"),
    ]

    def __init__(
        self,
        loop: AgenticLoop,
        model_name: str = "unknown",
        provider_type: str = "unknown",
        catalog: SkillCatalog | None = None,
        session_metadata: dict | None = None,
    ) -> None:
        super().__init__()
        self._agentic_loop = loop
        self._model_name = model_name
        self._provider_type = provider_type
        self._catalog = catalog
        self._session_metadata = session_metadata or {}
        self._turn_number = 0

        # Streaming state
        self._current_streaming_block: StreamingBlock | None = None
        self._current_spinner: ThinkingSpinner | None = None
        self._processing_task: asyncio.Task | None = None
        self._cancel_token: CancellationToken | None = None
        self._exiting: bool = False

        # Turn stats
        self._turn_start_time: float = 0.0
        self._turn_tool_count: int = 0
        self._turn_full_text: list[str] = []

    # ── Compose ──

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-view")
        # Build the slash command list for autocomplete
        commands = list(_BUILTIN_COMMANDS)
        skill_name_set: set[str] = set()
        if self._catalog is not None:
            for skill in self._catalog.all_skills():
                desc = skill.description
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                commands.append((f"/{skill.name}", desc))
                skill_name_set.add(skill.name)
        yield InputBox(
            commands=commands,
            skill_names=skill_name_set,
            id="input-wrapper",
        )

    async def on_mount(self) -> None:
        """Mount the welcome banner."""
        skill_names: list[str] | None = None
        if self._catalog is not None:
            skills = self._catalog.all_skills()
            if skills:
                skill_names = [s.name for s in skills]

        banner = WelcomeBanner(
            model_name=self._model_name,
            provider_type=self._provider_type,
            skill_names=skill_names,
        )
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        await chat.mount(banner)

        # Show resume indicator if session already has messages
        # (i.e., it was resumed via --resume before TUI launch)
        msg_count = len(self._agentic_loop._messages)
        if msg_count > 0:
            storage = self._agentic_loop.session_storage
            sid = storage.session_id if storage else "?"
            content = Text()
            content.append("\u2714 ", style=STATUS_SUCCESS)
            content.append(
                f"Resumed session {sid[:8]}... ({msg_count} messages loaded)",
                style=STATUS_SUCCESS,
            )
            await chat.mount(MessageBlock.command_result(content))

    # ── Frontend ABC ──

    async def run(self) -> None:  # type: ignore[override]
        """Start the Textual application."""
        await self.run_async()

    async def send_response(self, text: str) -> None:
        """Display a non-streaming response."""
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        block = MessageBlock.assistant_final(text)
        await chat.mount(block)
        self._scroll_to_bottom()

    # ── Input handling ──

    async def on_input_submitted(self, message: InputSubmitted) -> None:
        """Handle user input from the InputBox."""
        text = message.value

        # /quit ALWAYS works, even during execution
        if text.strip().lower() == _CMD_QUIT:
            await self._handle_quit()
            return

        # If currently processing, enqueue as mid-turn injection
        if self._processing_task is not None and not self._processing_task.done():
            self._agentic_loop.message_queue.enqueue(
                PendingMessage(content=text),
            )
            return

        # Slash commands
        if text.startswith("/"):
            should_continue = await self._handle_command(text)
            if not should_continue:
                self.exit()
            return

        # Normal message — process through the loop
        await self._start_turn(text)

    async def _handle_quit(self) -> None:
        """Gracefully shut down in response to /quit."""
        self._exiting = True
        if self._cancel_token:
            self._cancel_token.cancel()
        if self._processing_task is not None and not self._processing_task.done():
            self._processing_task.cancel()
            try:
                await asyncio.wait_for(
                    self._processing_task,
                    timeout=1.0,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.debug("Task cleanup during /quit", exc_info=True)
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        goodbye = MessageBlock.command_result(
            "Goodbye!",
            style="dim",
        )
        await chat.mount(goodbye)
        self.exit()

    # ── Turn processing ──

    async def _start_turn(
        self,
        user_input: str,
        *,
        display_text: str | None = None,
        skill_command: str | None = None,
    ) -> None:
        """Start processing a user turn.

        Args:
            user_input: The full text sent to the AgenticLoop.
            display_text: If provided, shown in the chat view instead of user_input.
            skill_command: If provided, the /skill prefix for colored display.
        """
        self._turn_number += 1
        self._turn_start_time = time.monotonic()
        self._turn_tool_count = 0
        self._turn_full_text.clear()

        # Display user message
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        if skill_command is not None:
            user_block = MessageBlock.skill_user(
                skill_command,
                display_text or user_input,
            )
        else:
            user_block = MessageBlock.user(display_text or user_input)
        await chat.mount(user_block)
        self._scroll_to_bottom()

        # Process in background
        self._cancel_token = CancellationToken()
        self._processing_task = asyncio.create_task(self._process_turn(user_input))

    async def _process_turn(self, user_input: str) -> None:
        """Background task: consume loop events and post to UI."""
        try:
            async for event in self._agentic_loop.process(
                user_input,
                cancel_token=self._cancel_token,
            ):
                if event.type == EventType.TOOL_START:
                    self._turn_tool_count += 1
                self.post_message(LoopEventMessage(event))
        except asyncio.CancelledError:
            logger.info("Turn cancelled by user")
            raise
        except Exception as e:
            logger.opt(exception=True).error(f"Turn error: {e}")
            self.post_message(
                LoopEventMessage(LoopEvent(type=EventType.ERROR, text=str(e)))
            )
        finally:
            self._cancel_token = None
            # Post a sentinel to finalize the turn footer
            self.post_message(
                LoopEventMessage(
                    LoopEvent(
                        type=EventType.TEXT_DONE,
                        text="__TURN_COMPLETE__",
                    )
                )
            )

    # ── Event dispatch ──

    async def on_loop_event_message(self, message: LoopEventMessage) -> None:
        """Route LoopEvent messages to the appropriate handler."""
        event = message.event

        handlers = {
            EventType.THINKING: self._handle_thinking,
            EventType.TEXT_DELTA: self._handle_text_delta,
            EventType.TEXT_DONE: self._handle_text_done,
            EventType.TOOL_START: self._handle_tool_start,
            EventType.TOOL_END: self._handle_tool_end,
            EventType.ERROR: self._handle_error,
            EventType.USER_MESSAGE_INJECTED: self._handle_injected,
        }
        handler = handlers.get(event.type)
        if handler is not None:
            await handler(event)

    async def _remove_spinner(self) -> None:
        """Remove the thinking spinner if present."""
        if self._current_spinner is not None:
            await self._current_spinner.remove()
            self._current_spinner = None

    async def _handle_thinking(self, event: LoopEvent) -> None:
        """Show ✻ spinner."""
        if self._current_spinner is not None:
            return
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        spinner = ThinkingSpinner()
        self._current_spinner = spinner
        await chat.mount(spinner)
        self._scroll_to_bottom()

    async def _handle_text_delta(self, event: LoopEvent) -> None:
        """Update streaming text block — flicker-free via append."""
        await self._remove_spinner()
        self._turn_full_text.append(event.text)

        if self._current_streaming_block is None:
            # First delta: create and mount the streaming block once
            chat = self.query_one(_CHAT_VIEW, VerticalScroll)
            block = StreamingBlock()
            self._current_streaming_block = block
            await chat.mount(block)

        # Append delta to existing widget — no remove/mount cycle
        self._current_streaming_block.append(event.text)
        self._scroll_to_bottom()

    async def _handle_text_done(self, event: LoopEvent) -> None:
        """Replace streaming block with final Markdown render."""
        await self._remove_spinner()

        # Sentinel check — the _process_turn sentinel
        if event.text == "__TURN_COMPLETE__":
            # Skip UI updates if the app is shutting down
            if self._exiting:
                self._processing_task = None
                return
            # Show turn footer
            elapsed = time.monotonic() - self._turn_start_time
            full_text = "".join(self._turn_full_text)
            if self._turn_number > 0:
                chat = self.query_one(_CHAT_VIEW, VerticalScroll)
                footer = MessageBlock.turn_footer(
                    self._turn_number,
                    self._turn_tool_count,
                    elapsed,
                    full_text,
                )
                await chat.mount(footer)
                self._scroll_to_bottom()
            self._processing_task = None
            return

        if not event.text:
            return

        chat = self.query_one(_CHAT_VIEW, VerticalScroll)

        # Remove streaming block, replace with final Markdown render
        if self._current_streaming_block is not None:
            await self._current_streaming_block.remove()
            self._current_streaming_block = None

        # Show interruption indicator with distinct styling
        if event.text == "(interrupted)":
            block = MessageBlock.command_result(
                "⏎ Interrupted",
                style="dim italic",
            )
            await chat.mount(block)
            self._scroll_to_bottom()
            return

        final_block = MessageBlock.assistant_final(event.text)
        await chat.mount(final_block)
        self._scroll_to_bottom()

    async def _handle_tool_start(self, event: LoopEvent) -> None:
        """Display tool call start."""
        await self._remove_spinner()
        args_str = self._format_tool_args(event.tool_args)
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        block = MessageBlock.tool_start(event.tool_name, args_str)
        await chat.mount(block)
        self._scroll_to_bottom()

    async def _handle_tool_end(self, event: LoopEvent) -> None:
        """Display tool result preview."""
        preview = event.tool_result_preview.replace("\n", " ")[:80]
        if preview:
            chat = self.query_one(_CHAT_VIEW, VerticalScroll)
            block = MessageBlock.tool_end(preview)
            await chat.mount(block)
            self._scroll_to_bottom()

    async def _handle_error(self, event: LoopEvent) -> None:
        """Display error message."""
        await self._remove_spinner()
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        block = MessageBlock.error(event.text)
        await chat.mount(block)
        self._scroll_to_bottom()

    async def _handle_injected(self, event: LoopEvent) -> None:
        """Display injected user message."""
        await self._remove_spinner()
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        block = MessageBlock.injected_user(event.text)
        await chat.mount(block)
        self._scroll_to_bottom()

    # ── Slash commands ──

    async def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True to continue, False to exit."""
        cmd = command.strip().lower()
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)

        handlers = {
            "/help": self._cmd_help,
            "/clear": self._cmd_clear,
            "/resume": self._cmd_resume,
            "/compact": self._cmd_compact,
            "/dream": self._cmd_dream,
        }
        handler = handlers.get(cmd)
        if handler:
            await handler(chat)
        else:
            await self._try_skill_command(command, chat)

        self._scroll_to_bottom()
        return True

    async def _cmd_help(self, chat: VerticalScroll) -> None:
        """Display help information."""
        for cmd, desc in _BUILTIN_COMMANDS:
            content = Text()
            content.append(cmd, style="bold cyan")
            content.append(f"  {desc}", style="dim")
            block = MessageBlock.command_result(content)
            await chat.mount(block)

        # Show skill commands
        if self._catalog is not None:
            skills = self._catalog.all_skills()
            if skills:
                await chat.mount(
                    MessageBlock.command_result(
                        Text("\nSkill commands:", style="bold"),
                    )
                )
                for skill in skills:
                    content = Text()
                    content.append(f"  /{skill.name}", style="bold green")
                    content.append(f"  {skill.description}", style="dim")
                    await chat.mount(MessageBlock.command_result(content))

    async def _cmd_clear(self, chat: VerticalScroll) -> None:
        """Clear conversation and start a new session."""
        self._agentic_loop.reset()
        self._turn_number = 0
        if self._agentic_loop.session_storage:
            await self._agentic_loop.session_storage.start_new_session()
        await chat.remove_children()
        skill_names = None
        if self._catalog is not None:
            skills = self._catalog.all_skills()
            if skills:
                skill_names = [s.name for s in skills]
        banner = WelcomeBanner(
            model_name=self._model_name,
            provider_type=self._provider_type,
            skill_names=skill_names,
        )
        await chat.mount(banner)
        result = MessageBlock.command_result("New session started.", style="green")
        await chat.mount(result)

    async def _cmd_compact(self, chat: VerticalScroll) -> None:
        """Run conversation compaction."""
        indicator = MessageBlock.command_result("compacting...", style="dim")
        await chat.mount(indicator)
        compacted = await self._agentic_loop._autocompactor.force_compact(
            self._agentic_loop._messages
        )
        if compacted is not None:
            self._agentic_loop._messages = compacted
            result = MessageBlock.command_result(
                "Conversation compacted.", style="green"
            )
        else:
            result = MessageBlock.command_result("Compaction not needed.", style="dim")
        await chat.mount(result)

    async def _cmd_dream(self, chat: VerticalScroll) -> None:
        """Run memory consolidation."""
        indicator = MessageBlock.command_result(
            "Running memory consolidation...", style="dim"
        )
        await chat.mount(indicator)
        runner = getattr(self._agentic_loop, "_auto_dream_runner", None)
        if runner is None:
            result = MessageBlock.command_result(
                "Auto-dream is not configured.", style="dim"
            )
        else:
            try:
                dream_result = await runner.force_run()
                if dream_result and dream_result.files_touched:
                    files_str = ", ".join(dream_result.files_touched)
                    result = MessageBlock.command_result(
                        f"Dream complete! Files: {files_str}",
                        style="green",
                    )
                else:
                    summary = dream_result.summary if dream_result else "No changes."
                    result = MessageBlock.command_result(f"{summary}", style="dim")
            except Exception as e:
                result = MessageBlock.command_result(f"Dream failed: {e}", style="red")
        await chat.mount(result)

    async def _cmd_resume(self, chat: VerticalScroll) -> None:
        """Show session picker to resume a previous session."""
        storage = self._agentic_loop.session_storage
        if storage is None:
            await chat.mount(
                MessageBlock.command_result(
                    "No session storage configured.", style="red"
                )
            )
            return

        sessions = await storage.list_sessions()
        if not sessions:
            await chat.mount(
                MessageBlock.command_result("No previous sessions found.", style="dim")
            )
            return

        # Use an Event to await the modal result
        result = _SessionPickResult()

        def _on_dismiss(session_id: str | None) -> None:
            result.session_id = session_id
            result.event.set()

        self.push_screen(SessionPickerScreen(sessions), callback=_on_dismiss)
        await result.event.wait()

        if result.session_id is None:
            await chat.mount(MessageBlock.command_result("Cancelled.", style="dim"))
            return

        try:
            # Resume the selected session
            self._agentic_loop.reset()
            self._turn_number = 0
            await self._agentic_loop.resume(result.session_id)

            # Clear chat and show resumed banner
            await chat.remove_children()
            skill_names = None
            if self._catalog is not None:
                skills = self._catalog.all_skills()
                if skills:
                    skill_names = [s.name for s in skills]
            banner = WelcomeBanner(
                model_name=self._model_name,
                provider_type=self._provider_type,
                skill_names=skill_names,
            )
            await chat.mount(banner)

            msg_count = len(self._agentic_loop._messages)
            content = Text()
            content.append("\u2714 ", style=STATUS_SUCCESS)
            content.append(
                f"Resumed session {result.session_id[:8]}... "
                f"({msg_count} messages loaded)",
                style=STATUS_SUCCESS,
            )
            await chat.mount(MessageBlock.command_result(content))
        except Exception:
            logger.opt(exception=True).error("Failed to resume session")
            await chat.mount(MessageBlock.error("Failed to resume session."))

    async def _try_skill_command(self, command: str, chat: VerticalScroll) -> None:
        """Try to execute a skill command; show 'Unknown command' if not a skill."""
        # Parse: "/mikan search frieren" → cmd_name="mikan", user_msg="search frieren"
        parts = command.strip().split(None, 1)
        cmd_name = parts[0].lstrip("/").lower() if parts else ""
        user_msg = parts[1] if len(parts) > 1 else ""

        if self._catalog is not None:
            skill = self._catalog.get_skill(cmd_name)
            if skill is not None:
                skill_content = self._catalog.get_skill_content(cmd_name) or ""
                augmented = self._build_skill_message(
                    skill_name=cmd_name,
                    skill_content=skill_content,
                    skill_base_dir=str(skill.base_dir),
                    user_message=user_msg,
                )
                await self._start_turn(
                    augmented,
                    display_text=user_msg,
                    skill_command=f"/{cmd_name}",
                )
                return

        # Not a skill — show unknown command
        result = MessageBlock.command_result(
            f"Unknown command: {command}", style="yellow"
        )
        await chat.mount(result)
        hint = MessageBlock.command_result(
            "Type /help to see available commands.", style="dim"
        )
        await chat.mount(hint)

    @staticmethod
    def _build_skill_message(
        skill_name: str,
        skill_content: str,
        skill_base_dir: str,
        user_message: str,
    ) -> str:
        """Build the augmented user message with skill context.

        Mirrors Claude Code's approach: skill content is injected as
        part of the user message so the model sees it in the current turn.
        """
        parts: list[str] = [
            f"<command-name>/{skill_name}</command-name>",
            f'<skill name="{skill_name}">',
            f"Base directory for this skill: {skill_base_dir}",
            "",
            skill_content,
            _SKILL_END_TAG,
        ]
        if user_message:
            parts.append("")
            parts.append(user_message)
        return "\n".join(parts)

    # ── Utility ──

    def _scroll_to_bottom(self) -> None:
        """Scroll the chat view to the bottom."""
        chat = self.query_one(_CHAT_VIEW, VerticalScroll)
        chat.scroll_end(animate=False)

    # ── Auto-focus ──

    def on_descendant_blur(self, event: DescendantBlur) -> None:
        """Auto-refocus input when focus escapes to chat area.

        Uses a tiny delay so we don't fight Textual's own focus
        transitions (e.g. during modal push/pop).
        """
        self.set_timer(0.05, self._refocus_input)

    def _refocus_input(self) -> None:
        """Refocus the input box unless a modal screen is active."""
        if self.screen.is_modal:
            return
        try:
            self.query_one("#input-wrapper", InputBox).focus_input()
        except Exception:
            logger.debug("Could not refocus input", exc_info=True)

    @staticmethod
    def _format_tool_args(args: dict) -> str:
        """Format tool arguments compactly for inline display."""
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

    # ── Actions ──

    def action_quit_app(self) -> None:
        """Handle Ctrl+D — exit the app, cancelling any active task."""
        self._exiting = True
        if self._processing_task is not None and not self._processing_task.done():
            if self._cancel_token:
                self._cancel_token.cancel()
            self._processing_task.cancel()
        self.exit()

    def action_cancel_turn(self) -> None:
        """Handle ESC / Ctrl+C — cancel current processing.

        Uses cooperative cancellation via the token first, which
        lets the loop emit ``(interrupted)`` and clean up gracefully.
        A fallback timer force-cancels the task if the cooperative
        path doesn't finish within 3 seconds.
        """
        if self._processing_task is not None and not self._processing_task.done():
            if self._cancel_token:
                self._cancel_token.cancel()
            # Schedule a fallback force-cancel in case cooperative
            # cancellation doesn't resolve quickly enough.
            self.set_timer(
                3.0,
                self._force_cancel_task,
            )
            logger.info("User cancelled current turn")

    def _force_cancel_task(self) -> None:
        """Fallback: force-cancel the processing task if still running.

        Called by a timer after cooperative cancellation was requested.
        """
        if self._processing_task is not None and not self._processing_task.done():
            self._processing_task.cancel()
            logger.warning("Cooperative cancellation timed out — force-cancelling task")
