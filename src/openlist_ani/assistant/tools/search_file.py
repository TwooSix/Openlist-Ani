"""
Search file tool with path sandboxing.
"""

from pathlib import Path
from typing import Any

from .base import BaseTool

_PROJECT_ROOT = Path.cwd().resolve()

_EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".egg-info",
    }
)

_MAX_RESULTS = 50


class SearchFileTool(BaseTool):
    """Tool for searching files by glob pattern within project."""

    @property
    def name(self) -> str:
        return "search_files"

    @property
    def description(self) -> str:
        return (
            "Search for files in the project directory using a glob "
            "pattern. Returns matching file paths relative to the "
            "project root. Use this to find files by name or extension. "
            "Examples: '**/*.py', 'src/**/*.toml', '**/SKILL.md'"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern to match files, e.g. '**/*.py', "
                        "'src/**/test_*.py', '**/SKILL.md'"
                    ),
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, pattern: str, **kwargs) -> str:
        """Search files matching glob pattern.

        Args:
            pattern: Glob pattern relative to project root.

        Returns:
            List of matching file paths or error message.
        """
        try:
            matches: list[str] = []
            for path in _PROJECT_ROOT.glob(pattern):
                if any(part in _EXCLUDED_DIRS for part in path.parts):
                    continue

                resolved = path.resolve()
                if not resolved.is_relative_to(_PROJECT_ROOT):
                    continue

                if resolved.is_file():
                    rel = resolved.relative_to(_PROJECT_ROOT)
                    matches.append(str(rel))

                if len(matches) >= _MAX_RESULTS:
                    break

            if not matches:
                return f"No files found matching pattern: {pattern}"

            matches.sort()
            result = f"Found {len(matches)} file(s) matching '{pattern}':\n"
            for m in matches:
                result += f"  {m}\n"

            if len(matches) >= _MAX_RESULTS:
                result += f"\n(results capped at {_MAX_RESULTS})"

            return result

        except Exception as exc:
            return f"Error searching files: {exc}"
