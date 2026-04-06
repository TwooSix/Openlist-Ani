"""
Rich CLI frontend for the assistant.

Interactive terminal UI using prompt_toolkit for input and Rich for output.

Key visual patterns:
- ❯ prompt character
- ✻ spinner with verb text while model is thinking
- ● dot for streaming text and completed assistant text
- ● dim dot for in-progress tools, green ● for completed
- ⎿ left bracket for tool results / sub-messages
- No Panel/Rule/Table borders — pure inline text
"""

from __future__ import annotations

from loguru import logger
import random
import time
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style as PTStyle
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner as RichSpinner
from rich.table import Table
from rich.text import Text

from openlist_ani.assistant.core.loop import AgenticLoop
from openlist_ani.assistant.core.models import EventType, LoopEvent
from openlist_ani.assistant.frontend.base import Frontend

if TYPE_CHECKING:
    from openlist_ani.assistant.skill.catalog import SkillCatalog

# ── Special characters ──
_POINTER = "\u276f"  # ❯ — prompt character
_BLACK_CIRCLE = "\u25cf"  # ● — tool/text status dot
_RESPONSE_PREFIX = "\u23bf"  # ⎿ — tool result sub-message prefix
_TEARDROP = "\u273b"  # ✻ — spinner character (TEARDROP_ASTERISK)

# Spinner verbs
_SPINNER_VERBS = [
    "Thinking", "Reasoning", "Processing", "Analyzing",
    "Pondering", "Considering", "Evaluating", "Computing",
    "Generating", "Synthesizing", "Formulating", "Crafting",
]

# Built-in slash commands with descriptions
_BUILTIN_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show available commands"),
    ("/clear", "Clear session history"),
    ("/reset", "Reset all memory"),
    ("/compact", "Compact conversation context"),
    ("/quit", "Exit the assistant"),
    ("/exit", "Exit the assistant"),
]

# prompt_toolkit style — minimal completion menu
# Non-selected items are dim, selected items use the "suggestion" color.
# No heavy backgrounds.
_PT_STYLE = PTStyle.from_dict(
    {
        # Completion menu: transparent-ish dark background
        "completion-menu": "bg:#1a1a2e",
        "completion-menu.completion": "fg:#888888 bg:#1a1a2e",
        "completion-menu.completion.current": "fg:#56b6c2 bg:#2a2a3e bold",
        # Meta (description) column
        "completion-menu.meta.completion": "fg:#555555 bg:#1a1a2e",
        "completion-menu.meta.completion.current": "fg:#7fbfbf bg:#2a2a3e",
    }
)


class SlashCommandCompleter(Completer):
    """Autocomplete for slash commands and skills.

    Triggers when the input starts with ``/``.  Yields built-in commands
    first, then discovered skill names -- each with a short description
    shown in the completion dropdown.
    """

    def __init__(self, catalog: SkillCatalog | None = None) -> None:
        self._entries: list[tuple[str, str]] = list(_BUILTIN_COMMANDS)

        if catalog is not None:
            for skill in catalog.all_skills():
                desc = skill.description
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                self._entries.append((f"/{skill.name}", desc))

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ):
        text = document.text_before_cursor

        if not text.startswith("/"):
            return

        for cmd, desc in self._entries:
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display_meta=desc,
                )


class CLIFrontend(Frontend):
    """Interactive CLI frontend with rich terminal UI."""

    def __init__(
        self,
        loop: AgenticLoop,
        model_name: str = "unknown",
        provider_type: str = "unknown",
        catalog: SkillCatalog | None = None,
    ) -> None:
        super().__init__(loop)
        self._console = Console()
        self._model_name = model_name
        self._provider_type = provider_type
        self._catalog = catalog
        self._turn_number = 0
        self._session: PromptSession[str] = PromptSession(
            history=InMemoryHistory(),
            completer=SlashCommandCompleter(catalog),
            complete_while_typing=True,
            style=_PT_STYLE,
        )

    async def run(self) -> None:
        """Start the interactive CLI session."""
        self._show_welcome()

        while True:
            try:
                with patch_stdout():
                    user_input = await self._session.prompt_async(
                        HTML(f"<ansicyan>{_POINTER}</ansicyan> "),
                    )

                if not user_input or not user_input.strip():
                    continue

                user_input = user_input.strip()

                if user_input.startswith("/"):
                    should_continue = await self._handle_command(user_input)
                    if not should_continue:
                        break
                    continue

                await self._process_with_events(user_input)

            except KeyboardInterrupt:
                self._console.print()
                continue
            except EOFError:
                self._show_goodbye()
                break
            except Exception as e:
                self._show_error(e)
                logger.opt(exception=True).error(f"CLI error: {e}")

    async def send_response(self, text: str) -> None:
        """Display a response in the terminal."""
        self._show_response(text)

    async def _process_with_events(self, user_input: str) -> None:
        """Process user input and render streaming events."""
        self._turn_number += 1
        start_time = time.monotonic()
        tool_call_count = 0

        # NOTE: Do NOT echo user message here. prompt_toolkit already leaves
        # the typed "❯ text" in the terminal scrollback when Enter is pressed,
        # so re-printing would cause duplication.

        full_text: list[str] = []
        live: Live | None = None
        spinner: Live | None = None
        streamed = False

        try:
            async for event in self._loop.process(user_input):
                if event.type == EventType.THINKING:
                    # ✻ + random verb + "..."
                    if spinner is None:
                        verb = random.choice(_SPINNER_VERBS)
                        spinner = Live(
                            RichSpinner(
                                "dots",
                                text=Text.assemble(
                                    (f" {verb}...", "dim italic"),
                                ),
                                style="cyan",
                            ),
                            console=self._console,
                            refresh_per_second=10,
                            transient=True,
                        )
                        spinner.start()

                elif event.type == EventType.TOOL_START:
                    if spinner is not None:
                        spinner.stop()
                        spinner = None
                    tool_call_count += 1
                    args_str = self._format_tool_args(event.tool_args)
                    # ToolUseLoader: dim ● when in-progress
                    line = Text()
                    line.append(f"{_BLACK_CIRCLE} ", style="dim")
                    line.append(event.tool_name, style="bold")
                    if args_str:
                        line.append(f"({args_str})", style="dim")
                    self._console.print(line)

                elif event.type == EventType.TOOL_END:
                    # Tool result: ⎿ prefix (MessageResponse)
                    preview = (
                        event.tool_result_preview.replace("\n", " ")[:80]
                    )
                    if preview:
                        self._console.print(
                            Text.assemble(
                                (f"  {_RESPONSE_PREFIX}  ", "dim"),
                                (preview, "dim"),
                            )
                        )

                elif event.type == EventType.TEXT_DELTA:
                    if spinner is not None:
                        spinner.stop()
                        spinner = None
                    streamed = True
                    full_text.append(event.text)
                    if live is None:
                        # transient=True: vanishes when stopped, replaced
                        # by final markdown render from TEXT_DONE.
                        live = Live(
                            self._render_streaming("".join(full_text)),
                            console=self._console,
                            refresh_per_second=15,
                            vertical_overflow="visible",
                            transient=True,
                        )
                        live.start()
                    else:
                        live.update(
                            self._render_streaming("".join(full_text))
                        )

                elif event.type == EventType.TEXT_DONE:
                    if spinner is not None:
                        spinner.stop()
                        spinner = None
                    if live is not None:
                        live.stop()
                        live = None
                    # Render final markdown (replaces transient stream)
                    if event.text:
                        self._render_final_text(event.text)
                    streamed = False
                    full_text.clear()

                elif event.type == EventType.ERROR:
                    if spinner is not None:
                        spinner.stop()
                        spinner = None
                    self._console.print(
                        Text.assemble(
                            (f"{_BLACK_CIRCLE} ", "red"),
                            (event.text, "red"),
                        )
                    )

        finally:
            if live is not None:
                live.stop()
            if spinner is not None:
                spinner.stop()

        elapsed = time.monotonic() - start_time
        self._show_footer(elapsed, tool_call_count, "".join(full_text))

    def _render_streaming(self, text: str) -> Text:
        """Render streaming text: ● + text."""
        result = Text()
        result.append(f"{_BLACK_CIRCLE} ", style="cyan bold")
        result.append(text)
        return result

    def _render_final_text(self, text: str) -> None:
        """Render completed assistant text: ● + markdown on the SAME line.

        AssistantTextMessage.tsx: ``<Box flexDirection="row">{dot}{markdown}</Box>``
        The dot occupies a 2-char column; markdown flows beside it.
        """
        self._console.print()
        try:
            md = Markdown(text)
        except Exception:
            md = Text(text)
        # Borderless table: 2-char dot column + flexible markdown column
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
        t.add_row(Text(_BLACK_CIRCLE, style="cyan bold"), md)
        self._console.print(t)

    def _format_tool_args(self, args: dict) -> str:
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

    def _show_welcome(self) -> None:
        """Display welcome banner."""
        self._console.print()
        self._console.print(
            f"  [bold cyan]{_TEARDROP}[/bold cyan] "
            f"[bold]{self._model_name}[/bold] [dim]({self._provider_type})[/dim]"
        )
        if self._catalog is not None:
            skill_names = [s.name for s in self._catalog.all_skills()]
            if skill_names:
                skills_str = ", ".join(skill_names)
                self._console.print(f"    [dim]skills: {skills_str}[/dim]")
        self._console.print(
            f"    [dim]/help for commands · Ctrl+D to exit[/dim]"
        )
        self._console.print()

    def _show_response(
        self, text: str, elapsed: float | None = None
    ) -> None:
        """Render the assistant's response (non-streaming fallback)."""
        if not text:
            return
        self._render_final_text(text)
        if elapsed is not None:
            self._show_footer(elapsed, 0, text)

    def _show_footer(
        self,
        elapsed: float,
        tool_call_count: int = 0,
        text: str = "",
    ) -> None:
        """Display compact stats footer — dim inline text."""
        self._console.print()
        parts: list[str] = []
        if self._turn_number > 0:
            parts.append(f"turn {self._turn_number}")
        turn_count = self._loop.turn_count
        total_tool_calls = turn_count + tool_call_count
        if total_tool_calls > 0:
            parts.append(
                f"{total_tool_calls} tool "
                f"{'call' if total_tool_calls == 1 else 'calls'}"
            )
        if elapsed < 1.0:
            parts.append(f"{elapsed * 1000:.0f}ms")
        else:
            parts.append(f"{elapsed:.1f}s")
        char_count = len(text)
        if char_count > 1000:
            parts.append(f"~{char_count / 1000:.1f}k chars")
        if parts:
            self._console.print(f"  [dim]{' · '.join(parts)}[/dim]")
        self._console.print()

    def _show_error(self, error: Exception) -> None:
        """Display error with red ● (ToolUseLoader isError=true)."""
        self._console.print()
        self._console.print(
            Text.assemble(
                (f"{_BLACK_CIRCLE} ", "red"),
                (f"{type(error).__name__}: {error}", "red"),
            )
        )
        self._console.print()

    def _show_goodbye(self) -> None:
        """Display goodbye message."""
        self._console.print()
        self._console.print("[dim]  Goodbye![/dim]")
        self._console.print()

    async def _handle_command(self, command: str) -> bool:
        """Handle slash commands. Returns True to continue, False to exit."""
        cmd = command.strip().lower()

        if cmd in ("/quit", "/exit"):
            self._show_goodbye()
            return False

        elif cmd == "/help":
            self._show_help()

        elif cmd == "/clear":
            memory = self._loop._memory
            await memory.clear_all()
            self._loop.reset()
            self._turn_number = 0
            self._console.print(
                Text.assemble(
                    (f"  {_RESPONSE_PREFIX}  ", "dim"),
                    ("Session history cleared.", "green"),
                )
            )

        elif cmd == "/reset":
            memory = self._loop._memory
            await memory.clear_all()
            self._loop.reset()
            self._turn_number = 0
            self._console.print(
                Text.assemble(
                    (f"  {_RESPONSE_PREFIX}  ", "dim"),
                    ("All memory has been reset.", "green"),
                )
            )

        elif cmd == "/compact":
            self._console.print(
                Text.assemble(
                    (f"{_BLACK_CIRCLE} ", "dim"),
                    ("compacting...", "dim"),
                )
            )
            compacted = await self._loop._autocompactor.force_compact(
                self._loop._messages
            )
            if compacted is not None:
                self._loop._messages = compacted
                self._console.print(
                    Text.assemble(
                        (f"  {_RESPONSE_PREFIX}  ", "dim"),
                        ("Conversation compacted.", "green"),
                    )
                )
            else:
                self._console.print(
                    Text.assemble(
                        (f"  {_RESPONSE_PREFIX}  ", "dim"),
                        ("Compaction not needed.", "dim"),
                    )
                )

        else:
            self._console.print(
                Text.assemble(
                    (f"  {_RESPONSE_PREFIX}  ", "dim"),
                    (f"Unknown command: {command}", "yellow"),
                )
            )
            self._console.print(
                f"  [dim]     Type /help to see available commands.[/dim]"
            )

        return True

    def _show_help(self) -> None:
        """Display help as clean inline text — no Table, no borders."""
        self._console.print()
        for cmd, desc in _BUILTIN_COMMANDS:
            if cmd == "/exit":
                continue
            self._console.print(
                f"  [cyan]{cmd:<12}[/cyan] [dim]{desc}[/dim]"
            )

        if self._catalog is not None:
            skills = self._catalog.all_skills()
            if skills:
                self._console.print()
                for skill in skills:
                    desc = skill.description
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    self._console.print(
                        f"  [cyan]/{skill.name:<11}[/cyan] [dim]{desc}[/dim]"
                    )

        self._console.print()
        self._console.print(f"  [dim]Ctrl+C  Cancel · Ctrl+D  Exit[/dim]")
        self._console.print()
