"""Update soul tool — adjust assistant personality via SOUL.md.

Allows the LLM to persist user-requested behaviour and personality
changes to the ``## User Customizations`` section of SOUL.md.
"""

from __future__ import annotations

from typing import Any

from ...logger import logger
from .base import BaseTool


class UpdateSoulTool(BaseTool):
    """Tool for saving personality/behaviour customizations to SOUL.md."""

    def __init__(self) -> None:
        self._memory_manager: Any = None

    @property
    def name(self) -> str:
        return "update_soul"

    @property
    def description(self) -> str:
        return (
            "Update the assistant's personality or behaviour rules "
            "(SOUL.md). Call this ONLY when the user **explicitly** asks "
            "you to change how you behave, communicate, or respond. "
            "Examples:\n"
            "- 'Keep replies short from now on' -> save communication style\n"
            "- 'Stop using emoji' -> save format constraint\n"
            "- 'You should proactively recommend new anime' -> save proactivity preference\n"
            "- 'Reply to me in Japanese' -> save language preference\n\n"
            "Do NOT call this unless the user explicitly requests a "
            "personality or behaviour change."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": (
                        "The behaviour or personality instruction to save, "
                        "e.g. 'Keep replies concise, no more than three sentences', "
                        "'Do not use emoji'"
                    ),
                },
            },
            "required": ["instruction"],
        }

    def set_memory_manager(self, manager: Any) -> None:
        """Inject the memory manager instance.

        Args:
            manager: An :class:`AssistantMemoryManager` instance.
        """
        self._memory_manager = manager

    async def execute(self, instruction: str = "", **kwargs: Any) -> str:
        """Persist one personality/behaviour instruction.

        Args:
            instruction: The customization to save.

        Returns:
            Confirmation or error message.
        """
        instruction = instruction.strip()
        if not instruction:
            return "No instruction provided."

        if self._memory_manager is None:
            logger.warning("UpdateSoulTool: memory manager not set")
            return "Soul update unavailable."

        try:
            await self._memory_manager.append_soul_customization(instruction)
            logger.info(f"UpdateSoulTool: saved customization: {instruction[:80]}")
            return f"✅ Got it, I'll remember: {instruction}"
        except Exception as exc:
            logger.exception("UpdateSoulTool: failed to save customization")
            return f"Failed to update soul: {exc}"
