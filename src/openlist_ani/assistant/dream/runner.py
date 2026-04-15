"""
Auto-dream runner — background memory consolidation via LLM.

The runner checks a cascade of gates (cheapest first) and, when all
pass, launches a simplified multi-turn LLM loop that reads session
transcripts, updates memory files, and prunes the MEMORY.md index.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from openlist_ani.assistant._constants import (
    DREAM_MAX_ROUNDS,
    DREAM_SCAN_INTERVAL_SECONDS,
)
from openlist_ani.assistant.core.models import Message, ProviderResponse, Role, ToolResult
from openlist_ani.assistant.dream.config import AutoDreamConfig
from openlist_ani.assistant.dream.lock import ConsolidationLock
from openlist_ani.assistant.dream.prompt import build_consolidation_prompt

if TYPE_CHECKING:
    from openlist_ani.assistant.provider.base import Provider


@dataclass
class DreamResult:
    """Result of a consolidation run."""

    files_touched: list[str] = field(default_factory=list)
    sessions_reviewed: int = 0
    summary: str = ""


# Read-only commands allowed in the dream agent's restricted shell
_ALLOWED_COMMANDS = frozenset({
    "ls", "find", "grep", "cat", "stat", "wc", "head", "tail", "rg",
})


class AutoDreamRunner:
    """Background memory consolidation runner.

    Call :meth:`maybe_run` after each model response to check gates.
    If all gates pass, runs the 4-phase consolidation in the background.
    """

    def __init__(
        self,
        config: AutoDreamConfig,
        provider: Provider,
        memory_dir: Path,
        sessions_dir: Path,
        data_dir: Path,
    ) -> None:
        self._config = config
        self._provider = provider
        self._memory_dir = memory_dir
        self._sessions_dir = sessions_dir
        self._lock = ConsolidationLock(data_dir)
        self._last_scan_time: float = 0.0

    @property
    def lock(self) -> ConsolidationLock:
        return self._lock

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def maybe_run(
        self,
        current_session_id: str,
    ) -> DreamResult | None:
        """Check gates and run consolidation if conditions are met.

        Returns a :class:`DreamResult` or ``None`` if not triggered.
        """
        # Gate 1: enabled?
        if not self._config.enabled:
            return None

        # Gate 2: time gate
        last_ts = await self._lock.read_last_consolidated_at()
        elapsed_hours = (time.time() - last_ts) / 3600 if last_ts > 0 else float("inf")
        if elapsed_hours < self._config.min_hours:
            return None

        # Gate 3: scan throttle
        now = time.time()
        if now - self._last_scan_time < DREAM_SCAN_INTERVAL_SECONDS:
            return None
        self._last_scan_time = now

        # Gate 4: session count
        session_ids = await self._lock.list_sessions_touched_since(
            last_ts, self._sessions_dir
        )
        # Exclude current session
        session_ids = [s for s in session_ids if s != current_session_id]
        if len(session_ids) < self._config.min_sessions:
            return None

        # Gate 5: acquire lock
        prior_mtime = await self._lock.try_acquire()
        if prior_mtime is None:
            return None

        # All gates passed — run consolidation
        try:
            result = await self._run_consolidation(session_ids)
            await self._lock.record_consolidation()
            return result
        except Exception as e:
            logger.error(f"Auto-dream consolidation failed: {e}")
            await self._lock.rollback(prior_mtime)
            return None

    async def force_run(self) -> DreamResult | None:
        """Force a consolidation run (for ``/dream`` command).

        Skips time and session gates. Only checks the lock.
        """
        last_ts = await self._lock.read_last_consolidated_at()
        session_ids = await self._lock.list_sessions_touched_since(
            last_ts, self._sessions_dir
        )

        if not session_ids:
            return DreamResult(summary="No sessions to consolidate.")

        prior_mtime = await self._lock.try_acquire()
        if prior_mtime is None:
            return DreamResult(summary="Another consolidation is in progress.")

        try:
            result = await self._run_consolidation(session_ids)
            await self._lock.record_consolidation()
            return result
        except Exception as e:
            logger.error(f"Dream consolidation failed: {e}")
            await self._lock.rollback(prior_mtime)
            return DreamResult(summary=f"Consolidation failed: {e}")

    # ------------------------------------------------------------------ #
    # Core consolidation loop
    # ------------------------------------------------------------------ #

    async def _run_consolidation(
        self,
        session_ids: list[str],
    ) -> DreamResult:
        """Execute the 4-phase consolidation via a simplified LLM loop.

        The dream agent gets:
        - The consolidation prompt
        - A restricted set of tools (read-only shell + file write in memory/)
        - Up to DREAM_MAX_ROUNDS of tool-call rounds
        """
        prompt = build_consolidation_prompt(
            memory_dir=str(self._memory_dir),
            sessions_dir=str(self._sessions_dir),
            session_ids=session_ids,
        )

        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=prompt),
            Message(role=Role.USER, content="Begin consolidation."),
        ]

        tool_defs = self._provider.format_raw_tools(
            self._get_dream_tool_definitions()
        )
        files_touched: list[str] = []

        for round_num in range(DREAM_MAX_ROUNDS):
            logger.debug(f"Dream round {round_num + 1}/{DREAM_MAX_ROUNDS}")

            response = await self._provider.chat_completion(
                messages=messages,
                tools=tool_defs,
                temperature=0.7,
            )

            # If no tool calls, we're done
            if not response.tool_calls:
                summary = response.text or "Consolidation complete."
                return DreamResult(
                    files_touched=files_touched,
                    sessions_reviewed=len(session_ids),
                    summary=summary,
                )

            # Process tool calls
            # Add assistant message with tool calls
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=response.text,
                    tool_calls=list(response.tool_calls),
                )
            )

            # Execute each tool call

            tool_results: list[ToolResult] = []
            for tc in response.tool_calls:
                result_text = await self._execute_dream_tool(
                    tc.name, tc.arguments, files_touched
                )
                tool_results.append(
                    ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result_text,
                    )
                )

            messages.append(
                Message(role=Role.TOOL, tool_results=tool_results)
            )

        # Hit max rounds
        return DreamResult(
            files_touched=files_touched,
            sessions_reviewed=len(session_ids),
            summary=f"Consolidation stopped after {DREAM_MAX_ROUNDS} rounds.",
        )

    # ------------------------------------------------------------------ #
    # Dream tool execution
    # ------------------------------------------------------------------ #

    async def _execute_dream_tool(
        self,
        tool_name: str,
        arguments: dict,
        files_touched: list[str],
    ) -> str:
        """Execute a restricted tool call for the dream agent."""
        if tool_name == "dream_shell":
            return await self._dream_shell(arguments.get("command", ""))
        elif tool_name == "dream_write":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            return await self._dream_write(path, content, files_touched)
        elif tool_name == "dream_read":
            path = arguments.get("path", "")
            return await self._dream_read(path)
        else:
            return f"Error: Unknown tool '{tool_name}'"

    async def _dream_shell(self, command: str) -> str:
        """Execute a read-only shell command."""
        if not command.strip():
            return "Error: Empty command"

        # Extract the base command (first word)
        parts = command.strip().split()
        base_cmd = parts[0]

        if base_cmd not in _ALLOWED_COMMANDS:
            return (
                f"Error: Command '{base_cmd}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(_ALLOWED_COMMANDS))}"
            )

        # Reject shell metacharacters to prevent command injection.
        # The allowlist check above only validates the first word, so
        # without this guard a command like "cat /etc/passwd; rm -rf /"
        # would pass (first word is "cat") and the shell would execute
        # the destructive second command.
        _SHELL_METACHARS = frozenset(";|&`$(){}!\\'\"\n")
        if _SHELL_METACHARS & set(command):
            return (
                "Error: Shell metacharacters are not allowed in "
                "dream commands. Use simple commands only."
            )

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                parts,
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self._memory_dir.parent),  # data_dir
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            # Truncate long output
            if len(output) > 10_000:
                output = output[:10_000] + "\n... (truncated)"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out (30s)"
        except Exception as e:
            return f"Error: {e}"

    async def _dream_write(
        self,
        path: str,
        content: str,
        files_touched: list[str],
    ) -> str:
        """Write a file — only within the memory/ directory."""
        if not path:
            return "Error: No path provided"

        # Resolve and verify it's within memory/
        try:
            target = Path(path).resolve()
            mem_dir = self._memory_dir.resolve()
            target.relative_to(mem_dir)
        except (ValueError, RuntimeError):
            return (
                f"Error: Path '{path}' is outside the memory directory. "
                f"Only writes within {self._memory_dir} are allowed."
            )

        try:
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, content, "utf-8")
            filename = target.name
            if filename not in files_touched:
                files_touched.append(filename)
            return f"Written: {filename}"
        except OSError as e:
            return f"Error writing {path}: {e}"

    async def _dream_read(self, path: str) -> str:
        """Read a file."""
        if not path:
            return "Error: No path provided"

        try:
            target = Path(path).resolve()
            content = await asyncio.to_thread(target.read_text, "utf-8")
            if len(content) > 10_000:
                content = content[:10_000] + "\n... (truncated)"
            return content or "(empty file)"
        except FileNotFoundError:
            return f"Error: File not found: {path}"
        except OSError as e:
            return f"Error reading {path}: {e}"

    # ------------------------------------------------------------------ #
    # Tool definitions
    # ------------------------------------------------------------------ #

    def _get_dream_tool_definitions(self) -> list[dict]:
        """Tool definitions for the dream agent (neutral format).

        Returns a list of dicts with ``name``, ``description``, and
        ``parameters`` keys.  The caller must pass these through
        ``Provider.format_raw_tools`` before sending to the API.
        """
        return [
            {
                "name": "dream_shell",
                "description": (
                    "Execute a read-only shell command. "
                    "Allowed commands: ls, find, grep, cat, stat, wc, head, tail, rg. "
                    "Working directory is the assistant data directory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to run",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "dream_read",
                "description": "Read a file's contents.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to the file to read",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "dream_write",
                "description": (
                    "Write content to a file. Only allowed within the "
                    f"memory directory ({self._memory_dir}). "
                    "Use YAML frontmatter for memory files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path within the memory directory",
                        },
                        "content": {
                            "type": "string",
                            "description": "File content to write",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        ]
