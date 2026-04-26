"""
Custom Textual widgets for the assistant TUI.

Widgets:
- WelcomeBanner     — startup header with logo and model info
- MessageBlock      — generic message container (user, assistant, tool, error)
- ThinkingSpinner   — animated spinner during model thinking
- CompletionOverlay — slash command preview list (below input)
- InputBox          — fixed bottom multi-line input with ❯ prompt, bordered by ────
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message as TextualMessage
from textual.reactive import reactive
from textual.widgets import Static, TextArea

from openlist_ani.assistant.frontend.textual_app.styles import (
    ACCENT_CYAN,
    ACCENT_PRIMARY,
    BORDER_COLOR,
    STATUS_ERROR,
    STATUS_WARNING,
    TEXT_DIM,
    TEXT_DIM_LIGHT,
    TEXT_PRIMARY,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult

# ── Special characters ──
_POINTER = "\u276f"  # ❯
_BLACK_CIRCLE = "\u25cf"  # ●
_RESPONSE_PREFIX = "\u23bf"  # ⎿
_TEARDROP = "\u273b"  # ✻

# Full-width horizontal rule character (same as Claude Code)
_HRULE_CHAR = "\u2500"  # ─

# Spinner verbs
_SPINNER_VERBS = [
    "Thinking",
    "Reasoning",
    "Processing",
    "Analyzing",
    "Pondering",
    "Considering",
    "Evaluating",
    "Computing",
    "Generating",
    "Synthesizing",
    "Formulating",
    "Crafting",
]

_DOT_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Max visible items in the completion overlay
_MAX_COMPLETIONS = 8

# CSS selector constants for InputBox child widgets
_ID_USER_INPUT = "user-input"
_ID_COMPLETION_OVERLAY = "completion-overlay"
_ID_COMMAND_TAG = "command-tag"
_SEL_USER_INPUT = f"#{_ID_USER_INPUT}"
_SEL_COMPLETION_OVERLAY = f"#{_ID_COMPLETION_OVERLAY}"
_SEL_COMMAND_TAG = f"#{_ID_COMMAND_TAG}"


class WelcomeBanner(Static):
    """Startup header showing model info. Scrolls away with content."""

    def __init__(
        self,
        model_name: str,
        provider_type: str,
        skill_names: list[str] | None = None,
    ) -> None:
        lines = Text()
        lines.append(f"\n  {_TEARDROP} ", style=f"bold {ACCENT_PRIMARY}")
        lines.append(model_name, style="bold")
        lines.append(f" ({provider_type})", style=TEXT_DIM)
        if skill_names:
            lines.append(f"\n    skills: {', '.join(skill_names)}", style=TEXT_DIM)
        lines.append("\n    /help for commands · Ctrl+D to exit\n", style=TEXT_DIM)
        super().__init__(lines)
        self.add_class("welcome-banner")


class MessageBlock(Static):
    """Single message block — user, assistant, tool, error, etc."""

    @staticmethod
    def user(text: str) -> MessageBlock:
        """Create a user message block."""
        content = Text()
        content.append(f"{_POINTER} ", style=f"bold {ACCENT_CYAN}")
        content.append(text, style=TEXT_PRIMARY)
        block = MessageBlock(content)
        block.add_class("msg-block", "msg-block--user")
        return block

    @staticmethod
    def skill_user(command: str, text: str) -> MessageBlock:
        """Create a user message block with a highlighted skill command prefix."""
        content = Text()
        content.append(f"{_POINTER} ", style=f"bold {ACCENT_CYAN}")
        content.append(command, style=f"bold {ACCENT_PRIMARY}")
        if text:
            content.append(f" {text}", style=TEXT_PRIMARY)
        block = MessageBlock(content)
        block.add_class("msg-block", "msg-block--user")
        return block

    @staticmethod
    def assistant_final(text: str) -> MessageBlock:
        """Create a final assistant message with Markdown rendering."""
        block = MessageBlock("")
        block.add_class("msg-block", "msg-block--assistant")
        try:
            md = RichMarkdown(text)
        except Exception:
            md = Text(text)
        from rich.table import Table

        t = Table(
            show_header=False,
            show_edge=False,
            show_lines=False,
            padding=0,
            box=None,
            expand=True,
        )
        t.add_column(width=2, no_wrap=True)
        t.add_column(ratio=1)
        t.add_row(Text(_BLACK_CIRCLE, style=f"bold {ACCENT_PRIMARY}"), md)
        block.update(t)
        return block

    @staticmethod
    def tool_start(name: str, args_str: str) -> MessageBlock:
        """Create a tool-start indicator."""
        content = Text()
        content.append(f"{_BLACK_CIRCLE} ", style=TEXT_DIM)
        content.append(name, style=f"bold {TEXT_PRIMARY}")
        if args_str:
            content.append(f"({args_str})", style=TEXT_DIM)
        block = MessageBlock(content)
        block.add_class("msg-block", "msg-block--tool-start")
        return block

    @staticmethod
    def tool_end(preview: str) -> MessageBlock:
        """Create a tool-result preview."""
        content = Text()
        content.append(f"  {_RESPONSE_PREFIX}  ", style=TEXT_DIM)
        content.append(preview, style=TEXT_DIM)
        block = MessageBlock(content)
        block.add_class("msg-block", "msg-block--tool-end")
        return block

    @staticmethod
    def error(text: str) -> MessageBlock:
        """Create an error message block."""
        content = Text()
        content.append(f"{_BLACK_CIRCLE} ", style=STATUS_ERROR)
        content.append(text, style=STATUS_ERROR)
        block = MessageBlock(content)
        block.add_class("msg-block", "msg-block--error")
        return block

    @staticmethod
    def injected_user(text: str) -> MessageBlock:
        """Create an injected-user-message indicator."""
        preview = text[:80] + "..." if len(text) > 80 else text
        content = Text()
        content.append(f"  {_RESPONSE_PREFIX}  ", style=TEXT_DIM)
        content.append("\U0001f4ac ", style="")
        content.append(f"[injected] {preview}", style=f"{STATUS_WARNING} dim")
        block = MessageBlock(content)
        block.add_class("msg-block", "msg-block--injected")
        return block

    @staticmethod
    def turn_footer(
        turn_number: int,
        tool_call_count: int,
        elapsed: float,
        text: str,
    ) -> MessageBlock:
        """Create a turn-stats footer line."""
        parts: list[str] = []
        if turn_number > 0:
            parts.append(f"turn {turn_number}")
        if tool_call_count > 0:
            noun = "call" if tool_call_count == 1 else "calls"
            parts.append(f"{tool_call_count} tool {noun}")
        if elapsed < 1.0:
            parts.append(f"{elapsed * 1000:.0f}ms")
        else:
            parts.append(f"{elapsed:.1f}s")
        char_count = len(text)
        if char_count > 1000:
            parts.append(f"~{char_count / 1000:.1f}k chars")
        content = Text(f"  {' · '.join(parts)}", style=TEXT_DIM)
        block = MessageBlock(content)
        block.add_class("turn-footer")
        return block

    @staticmethod
    def command_result(
        text: str | Text,
        style: str = "green",
    ) -> MessageBlock:
        """Create a command result message."""
        content = Text()
        content.append(f"  {_RESPONSE_PREFIX}  ", style=TEXT_DIM)
        if isinstance(text, Text):
            content.append(text)
        else:
            content.append(text, style=style)
        block = MessageBlock(content)
        block.add_class("cmd-result")
        return block


class StreamingBlock(Static):
    """Streaming output widget with flicker-free updates.

    Uses Textual's reactive system: appending text triggers
    watch__content which calls self.update() — a single
    efficient repaint with no DOM add/remove.

    Lifecycle:
    1. Created once when first TEXT_DELTA arrives
    2. append() called for each subsequent delta
    3. Removed and replaced by MessageBlock.assistant_final()
       when TEXT_DONE arrives
    """

    _content: reactive[str] = reactive("", layout=False)

    def __init__(self) -> None:
        super().__init__("")
        self.add_class("msg-block", "msg-block--assistant")

    def append(self, delta: str) -> None:
        """Append a text delta — triggers reactive update."""
        self._content += delta

    def watch__content(self, value: str) -> None:
        """Re-render when content changes."""
        content = Text()
        content.append(f"{_BLACK_CIRCLE} ", style=f"bold {ACCENT_PRIMARY}")
        content.append(value, style=TEXT_PRIMARY)
        self.update(content)

    @property
    def full_text(self) -> str:
        """Return the accumulated text."""
        return self._content


class ThinkingSpinner(Static):
    """Animated spinner shown while the model is thinking."""

    _frame_index: reactive[int] = reactive(0)

    def __init__(self) -> None:
        self._verb = secrets.choice(_SPINNER_VERBS)
        super().__init__("")
        self.add_class("thinking-spinner")

    def on_mount(self) -> None:
        self._render_frame()
        self._timer = self.set_interval(0.08, self._advance_frame)

    def _advance_frame(self) -> None:
        self._frame_index = (self._frame_index + 1) % len(_DOT_FRAMES)

    def watch__frame_index(self) -> None:
        self._render_frame()

    def _render_frame(self) -> None:
        frame_char = _DOT_FRAMES[self._frame_index % len(_DOT_FRAMES)]
        content = Text()
        content.append(f"{frame_char} ", style=f"{ACCENT_PRIMARY}")
        content.append(f"{self._verb}...", style=f"italic {TEXT_DIM_LIGHT}")
        self.update(content)


class InputSubmitted(TextualMessage):
    """Posted when the user presses Enter in the InputBox."""

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value


class TabCompleteRequested(TextualMessage):
    """Posted when user presses Tab — parent should fill selected completion."""

    pass


class CompletionNavigateUp(TextualMessage):
    """Posted when user presses Up while completions are visible."""

    pass


class CompletionNavigateDown(TextualMessage):
    """Posted when user presses Down while completions are visible."""

    pass


class ClearInputRequested(TextualMessage):
    """Posted when Escape is pressed — parent InputBox handles tag clearing."""

    pass


class SubmittableTextArea(TextArea):
    """A TextArea where Enter submits, Shift+Enter inserts newline, Tab completes.

    Up/Down arrows navigate the completion overlay when it is visible.
    """

    BINDINGS = [
        Binding("enter", "submit", "Submit", show=False),
        Binding("ctrl+u", "clear_input", "Clear", show=False),
        Binding("tab", "tab_complete", "Complete", show=False),
        Binding("up", "navigate_up", "Up", show=False),
        Binding("down", "navigate_down", "Down", show=False),
    ]

    # Set by InputBox to indicate whether the completion overlay is visible.
    completions_visible: bool = False
    # Set by InputBox to indicate an active command tag (allow empty submit).
    has_active_command: bool = False

    def action_submit(self) -> None:
        """Submit the current text."""
        text = self.text.strip()
        if text or self.has_active_command:
            self.post_message(InputSubmitted(text))
            self.clear()

    def action_clear_input(self) -> None:
        """Clear the input — delegate to parent for tag cleanup."""
        self.post_message(ClearInputRequested())

    def action_tab_complete(self) -> None:
        """Request tab completion from the parent."""
        self.post_message(TabCompleteRequested())

    def action_navigate_up(self) -> None:
        """Navigate up in completions, or default cursor movement."""
        if self.completions_visible:
            self.post_message(CompletionNavigateUp())
        else:
            # Default TextArea up-arrow behaviour (cursor up)
            self.action_cursor_up()

    def action_navigate_down(self) -> None:
        """Navigate down in completions, or default cursor movement."""
        if self.completions_visible:
            self.post_message(CompletionNavigateDown())
        else:
            # Default TextArea down-arrow behaviour (cursor down)
            self.action_cursor_down()

    def _on_key(self, event: Key) -> None:
        """Intercept keys to prevent default behaviour when needed."""
        if event.key == "enter":
            event.prevent_default()
            return
        if event.key == "tab":
            event.prevent_default()
            return
        # Intercept Up/Down when completions are visible so the
        # TextArea doesn't move the cursor.
        if event.key in ("up", "down") and self.completions_visible:
            event.prevent_default()
            return
        super()._on_key(event)


class CompletionOverlay(Static):
    """Displays matching slash commands below the input box.

    Rendered as a list of  ``/command  description`` lines, like Claude Code.
    Supports Up/Down arrow navigation with a highlighted selection.
    Hidden when there are no matches or input doesn't start with ``/``.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._matches: list[tuple[str, str]] = []
        self._selected_index: int = 0

    def set_matches(self, matches: list[tuple[str, str]]) -> None:
        """Update the visible completion list.

        Args:
            matches: List of (command, description) tuples.
        """
        self._matches = matches[:_MAX_COMPLETIONS]
        self._selected_index = 0
        if not self._matches:
            self.update("")
            self.display = False
            return

        self.display = True
        self._refresh_display()

    def _refresh_display(self) -> None:
        """Re-render the completion list with current selection highlighted."""
        content = Text()
        for idx, (cmd, desc) in enumerate(self._matches):
            # Truncate long descriptions
            if len(desc) > 72:
                desc = desc[:69] + "\u2026"
            if idx == self._selected_index:
                # Highlighted row
                content.append(f"  {cmd:<30}", style=f"bold {ACCENT_CYAN}")
                content.append(f"  {desc}\n", style=f"{TEXT_DIM_LIGHT}")
            else:
                content.append(f"  {cmd:<30}", style=TEXT_DIM_LIGHT)
                content.append(f"  {desc}\n", style=TEXT_DIM)
        self.update(content)

    def move_up(self) -> None:
        """Move selection up (with wrap)."""
        if not self._matches:
            return
        self._selected_index = (self._selected_index - 1) % len(self._matches)
        self._refresh_display()

    def move_down(self) -> None:
        """Move selection down (with wrap)."""
        if not self._matches:
            return
        self._selected_index = (self._selected_index + 1) % len(self._matches)
        self._refresh_display()

    @property
    def selected_match(self) -> str | None:
        """Return the currently selected command, or None."""
        if self._matches and 0 <= self._selected_index < len(self._matches):
            return self._matches[self._selected_index][0]
        return None

    @property
    def first_match(self) -> str | None:
        """Return the first matching command, or None."""
        if self._matches:
            return self._matches[0][0]
        return None

    @property
    def is_visible(self) -> bool:
        """Return whether the overlay has visible matches."""
        return bool(self._matches) and self.display


class HorizontalRule(Static):
    """A full-width horizontal rule (────────) like Claude Code uses."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)

    def on_mount(self) -> None:
        self._update_rule()

    def on_resize(self) -> None:
        self._update_rule()

    def _update_rule(self) -> None:
        width = self.size.width or 80
        rule_text = Text(_HRULE_CHAR * width, style=BORDER_COLOR)
        self.update(rule_text)


class InputBox(Vertical):
    """Fixed bottom input area with ❯ prompt, bordered by horizontal rules.

    Supports a command tag chip: when a skill command is selected
    (via Tab or manual typing), it is displayed as a styled tag
    between the ❯ and the text input.

    Layout::

        ────────────────────────
        ❯ /mikan  [input text]
        ────────────────────────
    """

    def __init__(
        self,
        commands: list[tuple[str, str]] | None = None,
        skill_names: set[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._commands: list[tuple[str, str]] = commands or []
        self._skill_names: set[str] = skill_names or set()
        self._active_command: str | None = None

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Update the available slash commands list."""
        self._commands = commands

    def set_skill_names(self, names: set[str]) -> None:
        """Update the set of known skill names (without / prefix)."""
        self._skill_names = names

    def compose(self) -> ComposeResult:
        yield HorizontalRule(id="top-rule")
        with Horizontal(id="input-row"):
            yield Static(f"{_POINTER} ", id="prompt-char")
            yield Static("", id=_ID_COMMAND_TAG)
            yield SubmittableTextArea(id=_ID_USER_INPUT)
        yield HorizontalRule(id="bottom-rule")
        yield CompletionOverlay(id=_ID_COMPLETION_OVERLAY)

    def on_mount(self) -> None:
        ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
        ta.show_line_numbers = False
        ta.tab_behavior = "focus"
        ta.soft_wrap = True
        ta.theme = "css"
        ta.focus()
        # Start hidden
        self.query_one(_SEL_COMPLETION_OVERLAY).display = False
        self.query_one(_SEL_COMMAND_TAG).display = False

    # ── Command tag helpers ──

    def _set_command_tag(self, cmd: str) -> None:
        """Show the command tag chip and update state."""
        self._active_command = cmd
        tag = self.query_one(_SEL_COMMAND_TAG, Static)
        tag.update(Text(cmd, style=f"bold {ACCENT_PRIMARY}"))
        tag.display = True
        ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
        ta.has_active_command = True

    def _clear_command_tag(self) -> None:
        """Hide the command tag chip and clear state."""
        self._active_command = None
        tag = self.query_one(_SEL_COMMAND_TAG, Static)
        tag.update("")
        tag.display = False
        ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
        ta.has_active_command = False

    def _is_skill_command(self, cmd: str) -> bool:
        """Check if a command (with / prefix) is a known skill."""
        name = cmd.lstrip("/")
        return name in self._skill_names

    # ── Event handlers ──

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Filter and show completions when input starts with /."""
        overlay = self.query_one(_SEL_COMPLETION_OVERLAY, CompletionOverlay)
        ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
        text = event.text_area.text

        # If a command tag is active, suppress completions
        if self._active_command is not None:
            overlay.set_matches([])
            ta.completions_visible = False
            return

        if not text.startswith("/"):
            overlay.set_matches([])
            ta.completions_visible = False
            return

        # Check if user typed a complete skill command + space
        # e.g. "/mikan " → activate tag
        if " " in text:
            parts = text.split(None, 1)
            cmd_part = parts[0]  # e.g. "/mikan"
            rest = parts[1] if len(parts) > 1 else ""
            if self._is_skill_command(cmd_part):
                self._set_command_tag(cmd_part)
                ta.clear()
                if rest:
                    ta.insert(rest)
                overlay.set_matches([])
                ta.completions_visible = False
                return

        # Filter commands matching the typed prefix
        prefix = text.lower()
        matches = [
            (cmd, desc)
            for cmd, desc in self._commands
            if cmd.lower().startswith(prefix)
        ]
        overlay.set_matches(matches)
        ta.completions_visible = overlay.is_visible

    def on_completion_navigate_up(self, message: CompletionNavigateUp) -> None:
        """Move selection up in the overlay."""
        overlay = self.query_one(_SEL_COMPLETION_OVERLAY, CompletionOverlay)
        overlay.move_up()

    def on_completion_navigate_down(self, message: CompletionNavigateDown) -> None:
        """Move selection down in the overlay."""
        overlay = self.query_one(_SEL_COMPLETION_OVERLAY, CompletionOverlay)
        overlay.move_down()

    def on_tab_complete_requested(self, message: TabCompleteRequested) -> None:
        """Fill the input with the selected matching command."""
        overlay = self.query_one(_SEL_COMPLETION_OVERLAY, CompletionOverlay)
        selected = overlay.selected_match
        if selected is not None:
            ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
            if self._is_skill_command(selected):
                # Skill command: show as tag, clear text area for message
                self._set_command_tag(selected)
                ta.clear()
            else:
                # Builtin command: fill in text area with trailing space
                ta.clear()
                ta.insert(selected + " ")
            # Hide the overlay after completion
            overlay.set_matches([])
            ta.completions_visible = False

    def on_input_submitted(self, message: InputSubmitted) -> None:
        """Reconstruct full value when command tag is active, then clean up."""
        overlay = self.query_one(_SEL_COMPLETION_OVERLAY, CompletionOverlay)
        overlay.set_matches([])
        ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
        ta.completions_visible = False

        if self._active_command is not None:
            # Reconstruct: "/mikan search frieren"
            user_text = message.value
            full_value = f"{self._active_command} {user_text}".strip()
            message.value = full_value
            self._clear_command_tag()

    def on_clear_input_requested(self, message: ClearInputRequested) -> None:
        """Clear both the command tag and the text area."""
        self._clear_command_tag()
        ta = self.query_one(_SEL_USER_INPUT, SubmittableTextArea)
        ta.clear()
        overlay = self.query_one(_SEL_COMPLETION_OVERLAY, CompletionOverlay)
        overlay.set_matches([])
        ta.completions_visible = False

    def focus_input(self) -> None:
        """Focus the text area."""
        self.query_one(_SEL_USER_INPUT, SubmittableTextArea).focus()
