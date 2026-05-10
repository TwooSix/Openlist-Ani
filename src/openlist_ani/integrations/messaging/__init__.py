"""Messaging platform integrations shared by assistant and notifications."""

from .models import InboundMessage, OutboundTarget
from .state_store import MessagingStateStore

__all__ = ["InboundMessage", "MessagingStateStore", "OutboundTarget"]
