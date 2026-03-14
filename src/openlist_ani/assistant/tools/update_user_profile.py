"""Update user profile tool — persist user info to USER.md.

Allows the LLM to record personal information about the user
(name, preferences, habits) into the user profile so that it
survives across conversations.
"""

from __future__ import annotations

from typing import Any

from ...logger import logger
from .base import BaseTool


class UpdateUserProfileTool(BaseTool):
    """Tool for saving personal user information to USER.md."""

    def __init__(self) -> None:
        self._memory_manager: Any = None

    @property
    def name(self) -> str:
        return "update_user_profile"

    @property
    def description(self) -> str:
        return (
            "Save personal information about the user to their profile "
            "(USER.md). Call this **immediately** whenever you discover:\n"
            "- User's name or how they want to be called\n"
            "- Anime preferences, favourite genres, favourite anime\n"
            "- Viewing habits (preferred quality, subtitle language, etc.)\n"
            "- Bangumi collection analysis results\n"
            "- Any other personal trait, habit, or preference\n\n"
            "Be proactive — don't wait for the user to ask you to remember."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The information to save. For section='observation', "
                        "a concise statement like '用户名字叫小明'. "
                        "For section='bangumi_preferences', a multi-line "
                        "analysis of the user's anime preferences."
                    ),
                },
                "section": {
                    "type": "string",
                    "enum": ["observation", "bangumi_preferences"],
                    "description": (
                        "'observation' to append a fact to Agent Observations "
                        "(name, habit, preference). "
                        "'bangumi_preferences' to replace the Bangumi "
                        "Preferences section with collection analysis. "
                        "Default: 'observation'."
                    ),
                },
            },
            "required": ["content"],
        }

    def set_memory_manager(self, manager: Any) -> None:
        """Inject the memory manager instance.

        Args:
            manager: An :class:`AssistantMemoryManager` instance.
        """
        self._memory_manager = manager

    async def execute(
        self,
        content: str = "",
        section: str = "observation",
        **kwargs: Any,
    ) -> str:
        """Persist user information to the appropriate section.

        Args:
            content: Content to save.
            section: Target section ('observation' or 'bangumi_preferences').

        Returns:
            Confirmation or error message.
        """
        content = content.strip()
        if not content:
            return "No content provided."

        if self._memory_manager is None:
            logger.warning("UpdateUserProfileTool: memory manager not set")
            return "User profile update unavailable."

        try:
            if section == "bangumi_preferences":
                await self._memory_manager.update_user_profile(content)
                logger.info(
                    "UpdateUserProfileTool: updated Bangumi preferences "
                    f"({len(content)} chars)"
                )
                return "✅ Bangumi preferences updated."

            await self._memory_manager.add_user_observation(content)
            logger.info(f"UpdateUserProfileTool: saved observation: {content[:80]}")
            return f"✅ User profile noted: {content}"
        except Exception as exc:
            logger.exception("UpdateUserProfileTool: failed to save")
            return f"Failed to update user profile: {exc}"
