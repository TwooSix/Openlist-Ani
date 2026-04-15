"""
Bridge between AgenticLoop events and Textual's message system.

Wraps LoopEvent objects so they can be posted into Textual's
event queue and handled by the App's message handlers.
"""

from __future__ import annotations

from textual.message import Message as TextualMessage

from openlist_ani.assistant.core.models import LoopEvent


class LoopEventMessage(TextualMessage):
    """Carry a LoopEvent through Textual's message bus."""

    def __init__(self, event: LoopEvent) -> None:
        super().__init__()
        self.event = event
