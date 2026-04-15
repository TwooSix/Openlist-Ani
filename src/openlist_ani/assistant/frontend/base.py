"""
Frontend abstract base class.

Defines the interface that all frontends (Telegram, CLI) must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openlist_ani.assistant.core.loop import AgenticLoop


class Frontend(ABC):
    """Abstract frontend for the assistant."""

    def __init__(self, loop: AgenticLoop) -> None:
        self._loop = loop

    @abstractmethod
    async def run(self) -> None:
        """Start the frontend event loop."""
        ...

    @abstractmethod
    async def send_response(self, text: str) -> None:
        """Send a response to the user.

        Args:
            text: The response text.
        """
        ...

    async def handle_message(self, user_text: str) -> None:
        """Process a user message through the agentic loop.

        Consumes LoopEvent objects and forwards text responses.

        Args:
            user_text: The user's input.
        """
        from openlist_ani.assistant.core.models import EventType

        async for event in self._loop.process(user_text):
            if event.type == EventType.TEXT_DONE:
                await self.send_response(event.text)
