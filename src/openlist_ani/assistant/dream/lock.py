"""
Consolidation lock — file-based locking for auto-dream.

Uses the lock file's **mtime** as the ``lastConsolidatedAt`` timestamp.
The file body contains the PID of the holder.  Stale detection (1 hour)
prevents permanent lock-out from crashed processes.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from loguru import logger

from openlist_ani.assistant._constants import DREAM_LOCK_STALE_SECONDS

LOCK_FILE = ".consolidate-lock"


class ConsolidationLock:
    """Process-level lock using file mtime as ``lastConsolidatedAt``."""

    def __init__(self, data_dir: Path) -> None:
        self._lock_path = data_dir / LOCK_FILE

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    # ------------------------------------------------------------------ #
    # Read state
    # ------------------------------------------------------------------ #

    async def read_last_consolidated_at(self) -> float:
        """Return mtime of lock file (seconds since epoch).

        Returns 0.0 if the lock file does not exist.
        """
        return await asyncio.to_thread(self._read_mtime_sync)

    def _read_mtime_sync(self) -> float:
        if not self._lock_path.is_file():
            return 0.0
        try:
            return self._lock_path.stat().st_mtime
        except OSError:
            return 0.0

    # ------------------------------------------------------------------ #
    # Acquire / release
    # ------------------------------------------------------------------ #

    async def try_acquire(self) -> float | None:
        """Attempt to acquire the consolidation lock.

        Writes the current PID and verifies ownership.  Returns the
        **prior mtime** (for rollback) on success, or ``None`` if
        another process holds the lock.
        """
        return await asyncio.to_thread(self._try_acquire_sync)

    def _try_acquire_sync(self) -> float | None:
        prior_mtime = 0.0

        if self._lock_path.is_file():
            try:
                stat = self._lock_path.stat()
                prior_mtime = stat.st_mtime
                # Check if the existing holder is stale
                age = time.time() - stat.st_mtime
                if age < DREAM_LOCK_STALE_SECONDS:
                    # Check if PID is still alive
                    try:
                        holder_pid = int(
                            self._lock_path.read_text(encoding="utf-8").strip()
                        )
                        if self._pid_alive(holder_pid):
                            logger.debug(
                                f"Lock held by PID {holder_pid} "
                                f"({age:.0f}s old, not stale)"
                            )
                            return None
                    except (ValueError, OSError):
                        pass  # Corrupted lock file, take over
                else:
                    logger.info(
                        f"Lock file stale ({age:.0f}s > "
                        f"{DREAM_LOCK_STALE_SECONDS}s), taking over"
                    )
            except OSError:
                pass

        # Write our PID
        pid = os.getpid()
        try:
            self._lock_path.write_text(str(pid), encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to write lock file: {e}")
            return None

        # Verify we still own it (race-condition guard)
        try:
            content = self._lock_path.read_text(encoding="utf-8").strip()
            if content != str(pid):
                logger.debug("Lock acquired by another process")
                return None
        except OSError:
            return None

        return prior_mtime

    async def rollback(self, prior_mtime: float) -> None:
        """Rewind mtime to pre-acquire value after a failed consolidation.

        This ensures the time gate isn't advanced when consolidation
        didn't complete successfully.
        """
        await asyncio.to_thread(self._rollback_sync, prior_mtime)

    def _rollback_sync(self, prior_mtime: float) -> None:
        if not self._lock_path.is_file():
            return
        try:
            if prior_mtime > 0:
                os.utime(self._lock_path, (prior_mtime, prior_mtime))
            else:
                self._lock_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to rollback lock mtime: {e}")

    async def record_consolidation(self) -> None:
        """Stamp the lock file with current time (updates mtime).

        Also used for manual ``/dream`` — creates the lock file if absent.
        """
        await asyncio.to_thread(self._record_sync)

    def _record_sync(self) -> None:
        pid = os.getpid()
        try:
            self._lock_path.write_text(str(pid), encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to record consolidation: {e}")

    # ------------------------------------------------------------------ #
    # Session scanning
    # ------------------------------------------------------------------ #

    async def list_sessions_touched_since(
        self,
        since: float,
        sessions_dir: Path,
    ) -> list[str]:
        """Return session IDs (JSONL filenames without extension) with
        mtime after *since* (seconds since epoch)."""
        return await asyncio.to_thread(
            self._list_sessions_sync, since, sessions_dir
        )

    def _list_sessions_sync(
        self, since: float, sessions_dir: Path
    ) -> list[str]:
        if not sessions_dir.is_dir():
            return []

        result: list[str] = []
        for path in sessions_dir.glob("*.jsonl"):
            try:
                if path.stat().st_mtime > since:
                    result.append(path.stem)
            except OSError:
                continue
        return result

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check if a process with the given PID is alive (cross-platform)."""
        # Try /proc filesystem first (Linux/macOS)
        proc_path = Path(f"/proc/{pid}")
        if proc_path.is_dir():
            return True
        # Fallback for systems without /proc (e.g. Windows):
        # conservative — assume process is dead so stale locks get reclaimed.
        # This is acceptable because the lock is best-effort anyway.
        return False
