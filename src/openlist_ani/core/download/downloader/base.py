from abc import ABC, abstractmethod

from ..task import DownloadTask


class DownloadError(Exception):
    """Raised when a download fails permanently."""


class BaseDownloader(ABC):
    @property
    @abstractmethod
    def downloader_type(self) -> str: ...

    @abstractmethod
    async def download(self, task: DownloadTask) -> None:
        """Execute the full download lifecycle for the given task.

        This single method handles everything: submitting the download,
        monitoring progress, post-processing (rename/move), and cleanup.

        Requirements:
            - Idempotent: safe to call again on the same task after restart.
              Check task.downloader_data and the backend for current state.
            - Set task.output_path before returning on success.
            - Raise DownloadError on failure.
            - Clean up resources in a finally block (handles cancellation too).
        """
