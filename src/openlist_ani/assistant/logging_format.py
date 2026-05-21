from __future__ import annotations

import json
from typing import Any

MAX_LOG_TEXT_CHARS = 300
MAX_LOG_VALUE_CHARS = 600


def format_log_text(text: object, max_chars: int = MAX_LOG_TEXT_CHARS) -> str:
    """Return a quoted, one-line text preview for user-facing logs."""
    compact = _one_line(str(text))
    return f'"{_truncate(compact, max_chars)}"'


def format_log_value(value: Any, max_chars: int = MAX_LOG_VALUE_CHARS) -> str:
    """Return a compact one-line representation for arguments and params."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return _truncate(_one_line(text), max_chars)


def format_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """Format a tool call as a user-readable flow log line."""
    if name == "skill_tool":
        skill_name = str(arguments.get("skill_name") or "").strip()
        action = str(arguments.get("action") or "default").strip()
        label = f"{skill_name}.{action}" if skill_name else action
        params = arguments.get("params")
        if isinstance(params, dict) and params:
            return f"Calling skill: {label} params={format_log_value(params)}"
        return f"Calling skill: {label}"

    if arguments:
        return f"Calling tool: {name} args={format_log_value(arguments)}"
    return f"Calling tool: {name}"


def _one_line(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."
