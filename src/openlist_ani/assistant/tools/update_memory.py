"""Update memory tool — persist valuable context to MEMORY.md.

Allows the LLM to save durable facts, decisions, and contextual
knowledge to long-term memory so they can inform future conversations.
"""

from __future__ import annotations

from typing import Any

from ...logger import logger
from .base import BaseTool


class UpdateMemoryTool(BaseTool):
    """Tool for saving contextual facts to MEMORY.md."""

    def __init__(self) -> None:
        self._memory_manager: Any = None

    @property
    def name(self) -> str:
        return "update_memory"

    @property
    def description(self) -> str:
        return (
            "Save valuable contextual information to long-term memory "
            "(MEMORY.md). Call this for knowledge that does NOT belong "
            "in the user profile or assistant personality, such as:\n"
            "- Important decisions or outcomes from conversations\n"
            "- Task results worth remembering (e.g. 'downloaded X anime')\n"
            "- Discovered facts about the user's setup or environment\n"
            "- Workflow patterns or recurring requests\n\n"
            "Do NOT use this for personal user info (use "
            "update_user_profile) or personality changes (use "
            "update_soul)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "A concise, durable fact to remember, e.g. "
                        "'用户的 qBittorrent 运行在 8080 端口', "
                        "'上次帮用户下载了《葬送的芙莉莲》全集'"
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "preference",
                        "constraint",
                        "identity",
                        "project_state",
                        "workflow",
                        "general",
                    ],
                    "description": ("Fact category. Default: 'general'."),
                },
            },
            "required": ["fact"],
        }

    def set_memory_manager(self, manager: Any) -> None:
        """Inject the memory manager instance.

        Args:
            manager: An :class:`AssistantMemoryManager` instance.
        """
        self._memory_manager = manager

    async def execute(
        self,
        fact: str = "",
        category: str = "general",
        **kwargs: Any,
    ) -> str:
        """Persist one fact to MEMORY.md.

        Args:
            fact: Content of the fact.
            category: Fact category.

        Returns:
            Confirmation or error message.
        """
        fact = fact.strip()
        if not fact:
            return "No fact provided."

        if self._memory_manager is None:
            logger.warning("UpdateMemoryTool: memory manager not set")
            return "Memory update unavailable."

        try:
            await self._memory_manager.add_memory_fact(
                content=fact,
                category=category,
            )
            logger.info(f"UpdateMemoryTool: saved fact [{category}]: {fact[:80]}")
            return f"✅ Memory saved: {fact}"
        except Exception as exc:
            logger.exception("UpdateMemoryTool: failed to save fact")
            return f"Failed to save memory: {exc}"
