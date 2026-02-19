from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..model.task import DownloadState, DownloadTask


@dataclass
class StateTransition:
    """Result of one state-handler execution in the download state machine.

        Field semantics:
        - success: Whether current handler logic succeeded.
            If False, manager marks task as failed and enters retry/failure flow.
        - next_state: Optional next state to switch to before deciding whether to continue.
        - delay_seconds: Delay before continuing (typically used by polling states).
        - error_message: Error reason used when success=False.

    Prefer semantic constructors (ok, fail, poll, transition) for readability.
    """

    success: bool
    next_state: Optional[DownloadState] = None
    delay_seconds: float = 0  # Delay before continuing (for polling)
    error_message: Optional[str] = None

    @classmethod
    def ok(
        cls,
        *,
        next_state: Optional[DownloadState] = None,
    ) -> "StateTransition":
        """Successful transition, optionally moving to a new state."""
        return cls(
            success=True,
            next_state=next_state,
        )

    @classmethod
    def transition(
        cls,
        next_state: DownloadState,
    ) -> "StateTransition":
        """Successful state switch to `next_state`."""
        return cls.ok(next_state=next_state)

    @classmethod
    def poll(cls, state: DownloadState, delay_seconds: float) -> "StateTransition":
        """Successful polling wait: re-dispatch in the same `state` later."""
        return cls(
            success=True,
            next_state=state,
            delay_seconds=delay_seconds,
        )

    @classmethod
    def fail(cls, error_message: str) -> "StateTransition":
        """Failed transition with error message."""
        return cls(
            success=False,
            error_message=error_message,
        )


class BaseDownloader(ABC):
    """Abstract base class for downloader implementations."""

    @property
    @abstractmethod
    def downloader_type(self) -> str:
        """Return the unique identifier for this downloader type."""
        pass

    @abstractmethod
    async def handle_pending(self, task: DownloadTask) -> StateTransition:
        """Handle PENDING state: prepare and start download."""
        pass

    @abstractmethod
    async def handle_downloading(self, task: DownloadTask) -> StateTransition:
        """Handle DOWNLOADING state: monitor progress."""
        pass

    @abstractmethod
    async def handle_downloaded(self, task: DownloadTask) -> StateTransition:
        """Handle DOWNLOADED state: post-process."""
        pass

    @abstractmethod
    async def handle_post_processing(self, task: DownloadTask) -> StateTransition:
        """Handle POST_PROCESSING state: cleanup and complete."""
        pass
