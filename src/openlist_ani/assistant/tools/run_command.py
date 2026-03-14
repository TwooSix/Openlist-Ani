"""
Run command tool with strict security sandboxing.

Supports running whitelisted system commands and executing
skill scripts via ``uv run python -m openlist_ani.assistant.skills.*``.
"""

import asyncio
import shlex
from typing import Any

from ...logger import logger
from .base import BaseTool

_COMMAND_TIMEOUT = 120  # seconds

# Commands explicitly allowed to execute
_ALLOWED_COMMANDS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "find",
        "wc",
        "df",
        "du",
        "ps",
        "uptime",
        "free",
        "echo",
        "date",
        "pwd",
        "sort",
        "uniq",
        "tr",
        "cut",
        "stat",
        "file",
        "tree",
        "uv",
    }
)

# Characters/patterns that indicate shell metacharacters
_FORBIDDEN_CHARS = frozenset(
    {
        "|",
        ">",
        "<",
        "&",
        ";",
        "`",
        "$(",
        "$((",
        "&&",
        "||",
    }
)

_MAX_OUTPUT_LEN = 4000

# Module prefix that ``uv run python -m`` is allowed to execute.
_ALLOWED_MODULE_PREFIX = "openlist_ani.assistant.skills."


def _validate_uv_command(tokens: list[str]) -> str | None:
    """Validate ``uv`` command tokens.

    Only ``uv run python -m openlist_ani.assistant.skills.*`` is
    permitted.  Arbitrary Python code execution is blocked.

    Args:
        tokens: Already split command tokens (first is ``uv``).

    Returns:
        Error message if invalid, None if OK.
    """
    # Minimum: uv run python -m <module>
    if len(tokens) < 5:
        return "uv command must follow: uv run python -m <skill_module> [args]"

    if tokens[1:4] != ["run", "python", "-m"]:
        return (
            "Only 'uv run python -m <module>' is allowed. "
            "Arbitrary uv commands are not permitted."
        )

    module_path = tokens[4]
    if not module_path.startswith(_ALLOWED_MODULE_PREFIX):
        return (
            f"Module must start with '{_ALLOWED_MODULE_PREFIX}'. Got: '{module_path}'"
        )

    # Block -c / --command / exec anywhere after uv
    for tok in tokens[5:]:
        lower = tok.lower()
        if lower in ("-c", "--command", "exec", "eval"):
            return f"Forbidden flag in uv command: '{tok}'"

    return None


def _validate_command(command: str) -> str | None:
    """Validate a command string for safety.

    Args:
        command: Raw command string.

    Returns:
        Error message if unsafe, None if safe.
    """
    stripped = command.strip()
    if not stripped:
        return "Empty command."

    for char in _FORBIDDEN_CHARS:
        if char in stripped:
            return (
                f"Command contains forbidden character/pattern: "
                f"'{char}'. Shell operators, pipes, and redirections "
                "are not allowed."
            )

    try:
        tokens = shlex.split(stripped)
    except ValueError as exc:
        return f"Invalid command syntax: {exc}"

    if not tokens:
        return "Empty command after parsing."

    cmd_name = tokens[0]

    if "/" in cmd_name:
        return (
            f"Command '{cmd_name}' contains a path separator. "
            "Only command names are allowed, not paths."
        )

    if cmd_name not in _ALLOWED_COMMANDS:
        return (
            f"Command '{cmd_name}' is not in the allowed list. "
            f"Allowed: {', '.join(sorted(_ALLOWED_COMMANDS))}"
        )

    # Extra validation for uv commands
    if cmd_name == "uv":
        return _validate_uv_command(tokens)

    return None


class RunCommandTool(BaseTool):
    """Tool for running whitelisted commands with security sandboxing."""

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Run a shell command in the project directory. "
            "Supports two modes:\n"
            "1) System commands: "
            + ", ".join(sorted(_ALLOWED_COMMANDS - {"uv"}))
            + "\n"
            "2) Skill scripts: "
            "'uv run python -m "
            "openlist_ani.assistant.skills.<skill>.script.<action> "
            "[--arg value ...]'\n"
            "Shell operators (|, >, &, ;) are forbidden."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Command to execute, e.g. "
                        "'ls -la src/' or "
                        "'uv run python -m "
                        "openlist_ani.assistant.skills.bangumi"
                        ".script.calendar'"
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, **kwargs) -> str:
        """Execute a validated command.

        Args:
            command: Shell command string.

        Returns:
            Command output or error message.
        """
        error = _validate_command(command)
        if error:
            return f"Error: {error}"

        tokens = shlex.split(command.strip())
        logger.info(f"RunCommandTool: Executing: {tokens}")

        try:
            process = await asyncio.create_subprocess_exec(
                *tokens,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=_COMMAND_TIMEOUT,
            )

        except asyncio.TimeoutError:
            process.kill()
            return f"Error: Command timed out after {_COMMAND_TIMEOUT} seconds."
        except FileNotFoundError:
            return f"Error: Command '{tokens[0]}' not found on this system."
        except Exception as exc:
            return f"Error executing command: {exc}"

        output_parts: list[str] = []

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        if stdout_text:
            output_parts.append(stdout_text)

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            output_parts.append(f"[stderr]\n{stderr_text}")

        if not output_parts:
            return "(no output)"

        result = "\n".join(output_parts)

        if process.returncode and process.returncode != 0:
            result = f"[exit code {process.returncode}]\n{result}"

        if len(result) > _MAX_OUTPUT_LEN:
            result = (
                result[:_MAX_OUTPUT_LEN]
                + f"\n... (truncated, {len(result)} chars total)"
            )

        return result
