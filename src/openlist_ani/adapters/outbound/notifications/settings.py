from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NotificationBotSettings:
    type: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool = False
    batch_interval: float = 300.0
    bots: list[NotificationBotSettings] = field(default_factory=list)
