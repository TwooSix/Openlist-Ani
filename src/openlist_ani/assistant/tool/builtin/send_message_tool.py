"""
SendMessageTool — pushes intermediate messages to the frontend.

Allows the model to send status updates or intermediate results
to the user during processing.
"""

from __future__ import annotations

from typing import Callable, Awaitable

from openlist_ani.assistant.tool.base import BaseTool

# Callback type: async function that receives a message string
MessageCallback = Callable[[str], Awaitable[None]]


class SendMessageTool(BaseTool):
    """Tool that sends intermediate messages to the user."""

    def __init__(self, callback: MessageCallback) -> None:
        self._callback = callback

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return (
            "Send an intermediate message to the user. "
            "Use this to provide progress updates during long-running tasks."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to the user.",
                },
            },
            "required": ["message"],
        }

    def is_concurrency_safe(self, tool_input: dict | None = None) -> bool:
        return True  # Sending messages doesn't modify state

    async def execute(self, **kwargs: object) -> str:
        message = str(kwargs.get("message", ""))
        if not message:
            return "Error: message is required."

        await self._callback(message)
        return "Message sent."
