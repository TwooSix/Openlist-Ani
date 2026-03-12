"""
Backward-compatibility shim.

The task model has been moved to ``openlist_ani.core.download.task``.
"""

from ..task import (
    STATE_TRANSITIONS,
    DownloadState,
    DownloadTask,
    InvalidStateTransitionError,
)

__all__ = [
    "DownloadState",
    "DownloadTask",
    "InvalidStateTransitionError",
    "STATE_TRANSITIONS",
]
