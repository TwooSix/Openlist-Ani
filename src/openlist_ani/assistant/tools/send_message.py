"""Send message tool for streaming progress to the user.

Provides a platform-agnostic ``send_message`` tool that the LLM can
invoke to push intermediate status updates (e.g. "Searching…") to the
user in real time.

The integration layer (Telegram, Discord, etc.) sets the callback via
:meth:`SendMessageTool.set_callback` before each conversation turn.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ...logger import logger
from .base import BaseTool

# Type alias for the callback that delivers a message to the user.
MessageCallback = Callable[[str], Awaitable[None]]


class SendMessageTool(BaseTool):
    """Tool for sending progress/intermediate messages to the user."""

    def __init__(self) -> None:
        self._callback: MessageCallback | None = None

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return (
            "Send a progress or intermediate message to the user. "
            "Use this to inform the user about what you are doing "
            "(e.g. searching, downloading, querying). "
            "The message is delivered immediately and does not "
            "replace your final response."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message text to send to the user.",
                },
            },
            "required": ["text"],
        }

    def set_callback(self, callback: MessageCallback | None) -> None:
        """Set the message delivery callback for the current turn.

        Called by the integration layer (Telegram, etc.) before each
        conversation turn.  Pass ``None`` to clear.

        Args:
            callback: Async callable that sends a string to the user.
        """
        self._callback = callback

    async def execute(self, text: str = "", **kwargs) -> str:
        """Send a progress message via the registered callback.

        Args:
            text: Message text to deliver.

        Returns:
            Confirmation string.
        """
        if not text:
            return "No message to send."

        if self._callback is not None:
            await self._callback(text)
            logger.debug(f"SendMessageTool: delivered '{text[:80]}...'")
        else:
            logger.warning("SendMessageTool: no callback set, message discarded")
            return "Message discarded (no callback)."

        return "Message sent."
