"""ReadFileTool — bounded, redacted read of project files.

Restricted to the directories declared in :mod:`_file_security`
(``src/`` ``skills/`` ``data/`` ``logs/`` ``memory/``) and never exposes
files whose name looks like a credential.  Output passes through
:func:`redact_secrets` so accidental matches are scrubbed before reaching
the LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from openlist_ani.assistant.tool.base import BaseTool

from ._file_security import (
    FileAccessDenied,
    is_likely_binary,
    redact_secrets,
    resolve_safe_path,
    short_path,
)

_DEFAULT_LIMIT = 2000
_MAX_LIMIT = 10_000
_MAX_FILE_BYTES = 3 * 1024 * 1024  # 3 MiB
_BINARY_SNIFF_BYTES = 1024


def _normalise_window(offset: int, limit: int) -> tuple[int, int] | str:
    """Validate ``offset`` / ``limit`` and clamp ``limit``.

    Returns the normalised pair or an error string.
    """
    if offset < 0:
        return "Error: 'offset' must be >= 0."
    if limit <= 0:
        return "Error: 'limit' must be > 0."
    return offset, min(limit, _MAX_LIMIT)


def _read_text_blocking(path: Path) -> tuple[str | None, str | None]:
    """Read a file synchronously (designed for ``asyncio.to_thread``).

    Returns ``(text, error)``.  Exactly one of the two is non-None.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
            if is_likely_binary(head):
                return None, (
                    f"Error: '{short_path(path)}' looks like a binary "
                    f"file; refusing to read."
                )
            rest = fh.read()
    except OSError as e:
        return None, f"Error reading file: {e}"
    return (head + rest).decode("utf-8", errors="replace"), None


def _format_listing(raw: str, path: Path, offset: int, limit: int) -> str:
    """Slice ``raw`` to a window, redact, and add line numbers + footer."""
    lines = raw.splitlines()
    total = len(lines)
    end = min(offset + limit, total)
    window = lines[offset:end]

    scrubbed, hits = redact_secrets("\n".join(window))
    scrubbed_lines = scrubbed.split("\n")

    width = max(4, len(str(end)))
    body = "\n".join(
        f"{i + 1:>{width}}\t{line}"
        for i, line in enumerate(scrubbed_lines, start=offset)
    )

    header = f"# {short_path(path)}  (lines {offset + 1}-{end} of {total})"
    notes: list[str] = []
    if end < total:
        notes.append(
            f"… {total - end} more line(s) not shown. Re-call with "
            f"offset={end} to continue."
        )
    if hits:
        notes.append(f"⚠ {hits} secret-like fragment(s) were redacted.")
    footer = ("\n\n" + "\n".join(notes)) if notes else ""

    return f"{header}\n{body}{footer}"


class ReadFileTool(BaseTool):
    """Read a UTF-8 text file and return numbered lines."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a project text file. Restricted to src/, skills/, data/, "
            "logs/, memory/. Refuses credential-like files; redacts secret "
            "patterns from output. Returns numbered lines."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path (relative to project root or absolute). "
                        "Must resolve inside src/, skills/, data/, logs/, "
                        "or memory/."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": ("0-based starting line. Default 0."),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum number of lines to return. "
                        f"Default {_DEFAULT_LIMIT}, max {_MAX_LIMIT}."
                    ),
                },
            },
            "required": ["path"],
        }

    @property
    def search_hint(self) -> str:
        return "read project file contents with line numbers"

    def is_read_only(self, tool_input: dict | None = None) -> bool:
        return True

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return True

    def get_activity_description(self, tool_input: dict | None = None) -> str | None:
        if tool_input and (p := tool_input.get("path")):
            return f"Reading {p}"
        return "Reading file"

    def prompt(self, tools=None) -> str:
        return (
            "## read_file\n"
            "- Use to inspect project source / docs / data inside the "
            "whitelist (src, skills, data, logs, memory).\n"
            "- Refuses paths outside the whitelist; refuses files whose "
            "name suggests credentials (.env, *secret*, *token*, *.pem, "
            "id_rsa, …).\n"
            "- Output is automatically scrubbed: lines containing tokens, "
            "API keys, bearer headers, private keys are replaced with "
            "<REDACTED>. Do not try to recover redacted values.\n"
            "- Use `offset` / `limit` for large files (default reads the "
            f"first {_DEFAULT_LIMIT} lines).\n"
        )

    async def execute(self, **kwargs: object) -> str:  # noqa: D401
        path_arg = kwargs.get("path")
        if not isinstance(path_arg, str):
            return "Error: 'path' must be a string."

        offset = int(kwargs.get("offset", 0) or 0)
        limit = int(kwargs.get("limit", _DEFAULT_LIMIT) or _DEFAULT_LIMIT)
        normalised = _normalise_window(offset, limit)
        if isinstance(normalised, str):
            return normalised
        offset, limit = normalised

        try:
            resolved = resolve_safe_path(path_arg)
        except FileAccessDenied as e:
            return f"Error: {e}"

        precheck = self._precheck(resolved)
        if precheck:
            return precheck

        raw, err = await asyncio.to_thread(_read_text_blocking, resolved)
        if err is not None:
            return err
        assert raw is not None  # noqa: S101 - invariant from helper contract

        return _format_listing(raw, resolved, offset, limit)

    @staticmethod
    def _precheck(path: Path) -> str | None:
        """Return an error string if the path can't be read, else None."""
        if not path.exists():
            return f"Error: File not found: {short_path(path)}"
        if path.is_dir():
            return (
                f"Error: '{short_path(path)}' is a directory. "
                "Use grep with a glob to inspect directories."
            )
        try:
            size = path.stat().st_size
        except OSError as e:
            return f"Error: cannot stat file: {e}"
        if size > _MAX_FILE_BYTES:
            return (
                f"Error: file too large ({size} bytes > "
                f"{_MAX_FILE_BYTES}). Use grep or pass a smaller "
                f"offset/limit window."
            )
        return None
