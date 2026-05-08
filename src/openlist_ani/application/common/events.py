from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class OAniEventType(StrEnum):
    TASK_CREATED = "task.created"
    TASK_STATE_CHANGED = "task.state_changed"
    DOWNLOAD_STARTED = "download.started"
    DOWNLOAD_COMPLETED = "download.completed"
    RENAME_COMPLETED = "rename.completed"
    NOTIFICATION_SENT = "notification.sent"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    PIPELINE_ERROR = "pipeline.error"


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class OAniEvent:
    event_type: OAniEventType
    payload: dict[str, Any] = field(default_factory=dict)
    severity: Severity = Severity.INFO
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str | None = None
