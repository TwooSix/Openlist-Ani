"""GrepTool — ripgrep-backed regex search restricted to whitelist roots.

Wraps the ``rg`` binary shipped by the PyPI ``ripgrep`` package (or any
``rg`` already on ``PATH``).  Every result line is passed through
:func:`redact_secrets` before reaching the LLM.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from openlist_ani.assistant.tool.base import BaseTool

from ._file_security import (
    FileAccessDenied,
    redact_secrets,
    resolve_safe_path,
    whitelist_roots,
)

_DEFAULT_HEAD_LIMIT = 250
_MAX_HEAD_LIMIT = 5_000
_OUTPUT_MODES = ("files_with_matches", "content", "count")
_RG_TIMEOUT_SECS = 30.0


class GrepTool(BaseTool):
    """Run ``rg`` over the whitelist roots and stream a trimmed result."""

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Regex search via ripgrep, restricted to src/, skills/, data/, "
            "logs/, memory/. Supports glob/type filters and content/file/"
            "count output modes. Output is automatically scrubbed of "
            "credential-like fragments."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "ripgrep regex pattern.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Optional path or sub-directory to search "
                        "(must be inside the whitelist).  Defaults to "
                        "every whitelist root."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": ("Glob filter, e.g. '**/*.py' (rg --glob)."),
                },
                "type": {
                    "type": "string",
                    "description": ("rg --type filter, e.g. 'py', 'md', 'json'."),
                },
                "output_mode": {
                    "type": "string",
                    "enum": list(_OUTPUT_MODES),
                    "description": ("files_with_matches (default) | content | count."),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "rg -i.",
                },
                "multiline": {
                    "type": "boolean",
                    "description": (
                        "Enable multiline / dotall (rg -U --multiline-dotall)."
                    ),
                },
                "context_before": {
                    "type": "integer",
                    "description": "rg -B (content mode only).",
                },
                "context_after": {
                    "type": "integer",
                    "description": "rg -A (content mode only).",
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum result lines/files. "
                        f"Default {_DEFAULT_HEAD_LIMIT}, max "
                        f"{_MAX_HEAD_LIMIT}."
                    ),
                },
            },
            "required": ["pattern"],
        }

    @property
    def search_hint(self) -> str:
        return "regex search across project files (ripgrep)"

    def is_read_only(self, tool_input: dict | None = None) -> bool:
        return True

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return True

    def get_activity_description(self, tool_input: dict | None = None) -> str | None:
        if tool_input and (p := tool_input.get("pattern")):
            preview = p if len(p) <= 40 else p[:37] + "…"
            return f"Searching for /{preview}/"
        return "Searching files"

    def prompt(self, tools=None) -> str:
        return (
            "## grep\n"
            "- ripgrep-backed regex search across src/, skills/, data/, "
            "logs/, memory/.\n"
            "- Pick the right output mode: `files_with_matches` to list "
            "files, `content` to see matching lines (with optional "
            "context_before / context_after), `count` for a tally.\n"
            "- Narrow the search with `path`, `glob`, or `type` whenever "
            "possible — broad searches over data/ or logs/ can be slow.\n"
            "- Output is scrubbed; do not infer the original token from "
            "<REDACTED> placeholders.\n"
        )

    # ── Implementation ────────────────────────────────────────────

    @staticmethod
    def _build_argv(rg: str, pattern: str, output_mode: str, kwargs: dict) -> list[str]:
        """Translate tool kwargs into a ripgrep argv (excluding paths)."""
        argv = [rg, "--no-follow", "--no-config"]

        if kwargs.get("case_insensitive"):
            argv.append("-i")
        if kwargs.get("multiline"):
            argv.extend(["-U", "--multiline-dotall"])

        glob = kwargs.get("glob")
        if isinstance(glob, str) and glob:
            argv.extend(["--glob", glob])
        ftype = kwargs.get("type")
        if isinstance(ftype, str) and ftype:
            argv.extend(["--type", ftype])

        if output_mode == "files_with_matches":
            argv.append("--files-with-matches")
        elif output_mode == "count":
            argv.append("--count")
        else:  # content
            argv.extend(["--line-number", "--no-heading", "--color", "never"])
            cb = int(kwargs.get("context_before") or 0)
            ca = int(kwargs.get("context_after") or 0)
            if cb > 0:
                argv.extend(["-B", str(cb)])
            if ca > 0:
                argv.extend(["-A", str(ca)])

        argv.append("--")
        argv.append(pattern)
        return argv

    @staticmethod
    async def _run_rg(argv: list[str]) -> tuple[int, bytes, bytes] | str:
        """Run ripgrep with a bounded timeout.

        Returns ``(returncode, stdout, stderr)`` on completion, or an
        error string on launch failure / timeout.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return f"Error launching ripgrep: {e}"
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_RG_TIMEOUT_SECS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return (
                f"Error: ripgrep timed out after {_RG_TIMEOUT_SECS:.0f}s. "
                "Narrow the search with path/glob/type."
            )
        return proc.returncode or 0, stdout, stderr

    @staticmethod
    def _format_output(text: str, head_limit: int) -> str:
        """Truncate, redact, and append a footer to ripgrep's stdout."""
        lines = text.splitlines()
        truncated = False
        if len(lines) > head_limit:
            lines = lines[:head_limit]
            truncated = True

        scrubbed, hits = redact_secrets("\n".join(lines))
        if hits:
            logger.info(f"GrepTool: redacted {hits} secret-like fragment(s) in result")

        footer_parts: list[str] = []
        if truncated:
            footer_parts.append(
                f"… truncated to head_limit={head_limit}; "
                f"narrow the query for more."
            )
        if hits:
            footer_parts.append(f"⚠ {hits} secret-like fragment(s) redacted.")
        footer = ("\n\n" + "\n".join(footer_parts)) if footer_parts else ""
        return scrubbed + footer

    async def execute(self, **kwargs: object) -> str:  # noqa: D401
        pattern = kwargs.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return "Error: 'pattern' must be a non-empty string."

        rg = shutil.which("rg")
        if rg is None:
            return (
                "Error: ripgrep (`rg`) binary not found on PATH. "
                "Install the `ripgrep` PyPI package or system rg."
            )

        output_mode = kwargs.get("output_mode") or "files_with_matches"
        if output_mode not in _OUTPUT_MODES:
            return f"Error: 'output_mode' must be one of {', '.join(_OUTPUT_MODES)}."

        head_limit = int(kwargs.get("head_limit") or _DEFAULT_HEAD_LIMIT)
        head_limit = max(1, min(head_limit, _MAX_HEAD_LIMIT))

        try:
            search_paths = self._resolve_search_paths(kwargs.get("path"))
        except FileAccessDenied as e:
            return f"Error: {e}"
        if not search_paths:
            return "Error: no whitelist directory exists to search."

        argv = self._build_argv(rg, pattern, output_mode, kwargs)
        argv.extend(str(p) for p in search_paths)

        outcome = await self._run_rg(argv)
        if isinstance(outcome, str):
            return outcome
        returncode, stdout, stderr = outcome

        # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
        if returncode not in (0, 1):
            err = stderr.decode("utf-8", errors="replace").strip()
            return f"Error: ripgrep failed ({returncode}): {err}"

        text = stdout.decode("utf-8", errors="replace")
        if not text.strip():
            return "(no matches)"

        return self._format_output(text, head_limit)

    def _resolve_search_paths(self, raw: object) -> list[Path]:
        """Resolve the optional ``path`` argument into one or more roots."""
        if raw is None or raw == "":
            return [p for p in whitelist_roots() if p.exists()]
        if not isinstance(raw, str):
            raise FileAccessDenied("'path' must be a string.")
        return [resolve_safe_path(raw)]
