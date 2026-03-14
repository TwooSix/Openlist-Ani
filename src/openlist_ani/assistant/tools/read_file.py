"""
Read file tool with path sandboxing.
"""

from pathlib import Path
from typing import Any

from .base import BaseTool

_PROJECT_ROOT = Path.cwd().resolve()

# File patterns that should never be read
_SENSITIVE_PATTERNS = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
    }
)

_SENSITIVE_EXTENSIONS = frozenset(
    {
        ".key",
        ".pem",
        ".p12",
        ".pfx",
        ".jks",
        ".keystore",
    }
)

_SENSITIVE_NAME_FRAGMENTS = frozenset(
    {
        "secret",
        "credential",
        "password",
        "private_key",
    }
)


def _is_path_safe(resolved: Path) -> str | None:
    """Validate that a resolved path is within the sandbox.

    Args:
        resolved: Resolved absolute path.

    Returns:
        Error message if unsafe, None if safe.
    """
    if not resolved.is_relative_to(_PROJECT_ROOT):
        return "Access denied: path is outside the project directory."

    name_lower = resolved.name.lower()

    if name_lower in _SENSITIVE_PATTERNS:
        return f"Access denied: '{resolved.name}' is a sensitive file."

    if resolved.suffix.lower() in _SENSITIVE_EXTENSIONS:
        return f"Access denied: '{resolved.suffix}' files may contain sensitive data."

    for fragment in _SENSITIVE_NAME_FRAGMENTS:
        if fragment in name_lower:
            return f"Access denied: filename contains '{fragment}'."

    return None


class ReadFileTool(BaseTool):
    """Tool for reading file contents with path sandboxing."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file within the project directory. "
            "Supports optional line range (start_line, end_line). "
            "Path is relative to the project root. "
            "Cannot read sensitive files (.env, *.key, *.pem, etc.)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Relative path to the file from project root, "
                        "e.g. 'src/openlist_ani/config.py'"
                    ),
                },
                "start_line": {
                    "type": "integer",
                    "description": (
                        "Start line number (1-based). Omit to read from the beginning."
                    ),
                },
                "end_line": {
                    "type": "integer",
                    "description": (
                        "End line number (1-based, inclusive). Omit to read to the end."
                    ),
                },
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        **kwargs,
    ) -> str:
        """Read file contents with sandboxing.

        Args:
            file_path: Relative path from project root.
            start_line: Optional start line (1-based).
            end_line: Optional end line (1-based, inclusive).

        Returns:
            File contents or error message.
        """
        try:
            resolved = (_PROJECT_ROOT / file_path).resolve()
        except (ValueError, OSError) as exc:
            return f"Error: Invalid file path: {exc}"

        error = _is_path_safe(resolved)
        if error:
            return f"Error: {error}"

        if not resolved.exists():
            return f"Error: File not found: {file_path}"

        if not resolved.is_file():
            return f"Error: Not a regular file: {file_path}"

        try:
            content = resolved.read_text("utf-8")
        except UnicodeDecodeError:
            return f"Error: Cannot read binary file: {file_path}"
        except OSError as exc:
            return f"Error: Cannot read file: {exc}"

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1) - 1
            e = min(total_lines, end_line or total_lines)
            lines = lines[s:e]
            header = (
                f"File: {file_path} "
                f"(lines {s + 1}-{min(s + len(lines), total_lines)} "
                f"of {total_lines})\n"
            )
        else:
            header = f"File: {file_path} ({total_lines} lines)\n"

        text = "".join(lines)
        if len(text) > 8000:
            text = text[:8000] + "\n... (truncated)"

        return header + text
