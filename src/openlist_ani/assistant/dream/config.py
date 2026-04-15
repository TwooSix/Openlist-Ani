"""
Auto-dream configuration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutoDreamConfig:
    """Configuration for the auto-dream memory consolidation system.

    Attributes:
        enabled: Whether auto-dream is active.
        min_hours: Minimum hours since last consolidation before triggering.
        min_sessions: Minimum number of new sessions since last consolidation.
    """

    enabled: bool = True
    min_hours: float = 24.0
    min_sessions: int = 5
