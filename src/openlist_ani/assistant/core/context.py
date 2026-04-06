"""
Context builder -- assembles the system prompt and message list.

System prompt assembly:
1. SOUL.md      -- Agent identity & behavioral rules (full system prompt)
2. CLAUDE.md    -- Project-level instructions (from project root)
3. MEMORY.md    -- Persistent long-term facts (injected as context)
4. USER.md      -- User profile / persona (injected as context)
5. Skill catalog listing
6. Environment info
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from typing import TYPE_CHECKING

from openlist_ani.assistant.core.models import Message, Role

if TYPE_CHECKING:
    from openlist_ani.assistant.memory.manager import MemoryManager
    from openlist_ani.assistant.skill.catalog import SkillCatalog
    from openlist_ani.assistant.tool.base import BaseTool


def _get_environment_section(
    model_name: str = "",
    provider_type: str = "",
) -> str:
    """Build environment info section."""
    cwd = str(__import__("pathlib").Path.cwd())
    os_info = f"{platform.system()} {platform.release()}"
    py_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}"
        f".{sys.version_info.micro}"
    )
    date_str = datetime.now().strftime("%Y-%m-%d")

    items = [
        f"Primary working directory: {cwd}",
        f"Platform: {platform.system()}",
        f"OS Version: {os_info}",
        f"Python: {py_version}",
        f"Current date: {date_str}",
    ]
    if model_name:
        items.append(f"Model: {model_name}")
    if provider_type:
        items.append(f"Provider: {provider_type}")

    bullets = "\n".join(f" - {item}" for item in items)
    return (
        f"# Environment\nYou have been invoked in the following environment:\n"
        f"{bullets}"
    )


class ContextBuilder:
    """Assembles the full message list for the LLM.

    Uses the four-file memory system:
    - SOUL.md as the base system prompt
    - MEMORY.md, USER.md injected as context sections
    - CLAUDE.md for project instructions
    """

    def __init__(
        self,
        memory: MemoryManager,
        catalog: SkillCatalog | None = None,
        model_name: str = "",
        provider_type: str = "",
        context_window_tokens: int | None = None,
        tools: list[BaseTool] | None = None,
    ) -> None:
        self._memory = memory
        self._catalog = catalog
        self._model_name = model_name
        self._provider_type = provider_type
        self._context_window_tokens = context_window_tokens
        self._tools = tools or []

    async def build_system(self) -> list[Message]:
        """Build only the system message(s), without a user message.

        Used by AgenticLoop to initialize the conversation once,
        then user messages are appended separately per turn.

        Returns:
            List containing the system message.
        """
        system_parts: list[str] = []

        # 1. SOUL.md -- the full system prompt / agent identity
        soul = self._memory.load_soul()
        if soul.strip():
            system_parts.append(soul.strip())

        # 2. CLAUDE.md instructions (project-level)
        claude_md_prompt = self._memory.build_claude_md_prompt()
        if claude_md_prompt:
            system_parts.append(claude_md_prompt)

        # 3. MEMORY.md -- persistent long-term facts
        memory_content = self._memory.load_memory()
        if memory_content.strip():
            system_parts.append(
                "# Persistent Memory\n\n"
                "The following are facts you have remembered across sessions. "
                "Use them to maintain continuity.\n\n"
                f"{memory_content.strip()}"
            )

        # 4. USER.md -- user profile
        user_content = self._memory.load_user()
        if user_content.strip():
            system_parts.append(
                "# User Profile\n\n"
                "The following is what you know about the user. "
                "Use this to personalize responses.\n\n"
                f"{user_content.strip()}"
            )

        # 5. Session history -- load today's session log so the model
        #    has context from prior turns (survives restarts).
        session_history = await self._memory.load_session_history()
        if session_history.strip():
            system_parts.append(
                "# Session History\n\n"
                "The following is a log of earlier conversation turns "
                "from the current session. Use it to maintain context "
                "and avoid repeating yourself.\n\n"
                f"{session_history.strip()}"
            )

        # 6. Skill catalog
        if self._catalog:
            catalog_prompt = self._catalog.build_catalog_prompt(
                context_window_tokens=self._context_window_tokens,
            )
            if catalog_prompt:
                system_parts.append(
                    f"<available_skills>\n{catalog_prompt}\n</available_skills>"
                )

        # 7. Per-tool prompt contributions
        tool_prompts: list[str] = []
        for tool in self._tools:
            if tool.is_enabled():
                prompt_text = await tool.prompt(tools=self._tools)
                if prompt_text.strip():
                    tool_prompts.append(prompt_text.strip())
        if tool_prompts:
            system_parts.append("\n\n".join(tool_prompts))

        # 8. Environment info
        system_parts.append(
            _get_environment_section(self._model_name, self._provider_type)
        )

        return [Message(role=Role.SYSTEM, content="\n\n".join(system_parts))]

    async def build(self, user_message: str) -> list[Message]:
        """Build the complete message list for a new user turn.

        This creates a fresh [system, user] pair — used by tests and
        standalone invocations. For multi-turn conversations, use
        build_system() + append user messages manually.
        """
        messages = await self.build_system()
        messages.append(Message(role=Role.USER, content=user_message))
        return messages
