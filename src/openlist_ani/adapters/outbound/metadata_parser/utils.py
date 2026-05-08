"""Utility functions for parser tools."""

import re


def parse_json_from_markdown(text: str) -> str | None:
    """Extract JSON string from markdown code block or plain text.

    Args:
        text: Text containing JSON, possibly in markdown code blocks

    Returns:
        Extracted JSON string or None if not found
    """
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return text[start:end]
    except ValueError:
        return None


def parse_json_array_from_markdown(text: str) -> str | None:
    """Extract JSON array string from markdown code block or plain text.

    Looks for a JSON array (starting with '[') in markdown code blocks or raw text.

    Args:
        text: Text containing JSON array, possibly in markdown code blocks

    Returns:
        Extracted JSON array string or None if not found
    """
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("["):
            return candidate

    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return text[start:end]
    except ValueError:
        return None
