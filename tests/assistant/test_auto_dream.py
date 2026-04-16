"""Tests for ConsolidationLock and AutoDreamRunner."""

from __future__ import annotations

import os
import time

import pytest

from openlist_ani.assistant.dream.config import AutoDreamConfig
from openlist_ani.assistant.dream.lock import ConsolidationLock
from openlist_ani.assistant.dream.prompt import build_consolidation_prompt
from openlist_ani.assistant.dream.runner import AutoDreamRunner, DreamResult


# ------------------------------------------------------------------ #
# ConsolidationLock
# ------------------------------------------------------------------ #


class TestConsolidationLock:
    @pytest.fixture
    def lock(self, tmp_path):
        return ConsolidationLock(tmp_path)

    @pytest.mark.asyncio
    async def test_read_last_consolidated_no_file(self, lock: ConsolidationLock):
        ts = await lock.read_last_consolidated_at()
        assert ts == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_acquire_creates_lock(self, lock: ConsolidationLock):
        prior = await lock.try_acquire()
        assert prior is not None
        assert prior == pytest.approx(0.0)  # No prior lock
        assert lock.lock_path.is_file()

    @pytest.mark.asyncio
    async def test_acquire_writes_pid(self, lock: ConsolidationLock):
        await lock.try_acquire()
        content = lock.lock_path.read_text().strip()
        assert content == str(os.getpid())

    @pytest.mark.asyncio
    async def test_record_consolidation(self, lock: ConsolidationLock):
        await lock.record_consolidation()
        ts = await lock.read_last_consolidated_at()
        assert ts > 0
        assert time.time() - ts < 5  # Should be very recent

    @pytest.mark.asyncio
    async def test_rollback_mtime(self, lock: ConsolidationLock):
        # Create a lock file with a different (dead) PID to simulate prior holder
        lock.lock_path.write_text("99999999", encoding="utf-8")
        original_ts = time.time() - 100
        os.utime(lock.lock_path, (original_ts, original_ts))

        # Acquire (takes over from dead PID, returns prior mtime)
        prior = await lock.try_acquire()
        assert prior is not None

        # Rollback should restore original mtime
        await lock.rollback(original_ts)
        restored_ts = await lock.read_last_consolidated_at()
        assert abs(restored_ts - original_ts) < 1.0

    @pytest.mark.asyncio
    async def test_rollback_removes_if_no_prior(self, lock: ConsolidationLock):
        await lock.try_acquire()
        await lock.rollback(0.0)
        assert not lock.lock_path.exists()

    @pytest.mark.asyncio
    async def test_blocked_by_live_process(self, lock: ConsolidationLock):
        """Lock held by current process should block re-acquisition."""
        # Write lock with current PID (simulating another holder)
        lock.lock_path.write_text(str(os.getpid()), encoding="utf-8")

        # Try to acquire — should fail because PID is alive
        prior = await lock.try_acquire()
        assert prior is None

    @pytest.mark.asyncio
    async def test_stale_lock_taken_over(self, lock: ConsolidationLock):
        """Stale lock (>1h) should be taken over."""
        lock.lock_path.write_text("99999999", encoding="utf-8")
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(lock.lock_path, (old_time, old_time))

        prior = await lock.try_acquire()
        assert prior is not None

    @pytest.mark.asyncio
    async def test_list_sessions_touched_since(self, lock: ConsolidationLock, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Create session files
        (sessions_dir / "old.jsonl").write_text("{}")
        old_time = time.time() - 86400 * 2
        os.utime(sessions_dir / "old.jsonl", (old_time, old_time))

        (sessions_dir / "new.jsonl").write_text("{}")

        # Only the new one should be returned
        since = time.time() - 86400  # 1 day ago
        result = await lock.list_sessions_touched_since(since, sessions_dir)
        assert "new" in result
        assert "old" not in result


# ------------------------------------------------------------------ #
# Consolidation prompt
# ------------------------------------------------------------------ #


class TestConsolidationPrompt:
    def test_prompt_contains_phases(self):
        prompt = build_consolidation_prompt(
            memory_dir="/data/memory",
            sessions_dir="/data/sessions",
            session_ids=["s1", "s2", "s3"],
        )
        assert "Phase 1" in prompt
        assert "Phase 2" in prompt
        assert "Phase 3" in prompt
        assert "Phase 4" in prompt
        assert "/data/memory" in prompt
        assert "/data/sessions" in prompt
        assert "- s1" in prompt
        assert "(3)" in prompt


# ------------------------------------------------------------------ #
# AutoDreamRunner gate checks
# ------------------------------------------------------------------ #


class TestAutoDreamRunnerGates:
    @pytest.fixture
    def setup(self, tmp_path):
        """Create runner with minimal config and mock provider."""
        from unittest.mock import AsyncMock, MagicMock

        config = AutoDreamConfig(enabled=True, min_hours=24.0, min_sessions=2)
        provider = AsyncMock()
        provider.chat_completion = AsyncMock(
            return_value=type(
                "R", (), {"text": "Done", "tool_calls": [], "stop_reason": "stop", "usage": {}}
            )()
        )
        provider.format_raw_tools = MagicMock(return_value=[])

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        memory_dir = data_dir / "memory"
        memory_dir.mkdir()
        sessions_dir = data_dir / "sessions"
        sessions_dir.mkdir()

        runner = AutoDreamRunner(
            config=config,
            provider=provider,
            memory_dir=memory_dir,
            sessions_dir=sessions_dir,
            data_dir=data_dir,
        )
        return runner, sessions_dir, data_dir

    @pytest.mark.asyncio
    async def test_disabled_config(self, tmp_path):
        from unittest.mock import AsyncMock

        config = AutoDreamConfig(enabled=False)
        runner = AutoDreamRunner(
            config=config,
            provider=AsyncMock(),
            memory_dir=tmp_path / "memory",
            sessions_dir=tmp_path / "sessions",
            data_dir=tmp_path,
        )
        result = await runner.maybe_run("current")
        assert result is None

    @pytest.mark.asyncio
    async def test_time_gate_blocks(self, setup):
        runner, _sessions_dir, _data_dir = setup
        # Record consolidation now — time gate should block
        await runner.lock.record_consolidation()

        result = await runner.maybe_run("current")
        assert result is None

    @pytest.mark.asyncio
    async def test_session_gate_blocks(self, setup):
        runner, sessions_dir, _data_dir = setup
        # Only 1 session (below min_sessions=2)
        (sessions_dir / "s1.jsonl").write_text("{}")

        await runner.maybe_run("current")
        # Should pass time gate (no prior consolidation = infinite hours)
        # but fail session gate (only 1 session, not the current one)
        # Actually, we need to make sure the scan throttle doesn't block
        runner._last_scan_time = 0
        result = await runner.maybe_run("current")
        assert result is None

    @pytest.mark.asyncio
    async def test_all_gates_pass(self, setup):
        runner, sessions_dir, _data_dir = setup
        # Create enough sessions
        (sessions_dir / "s1.jsonl").write_text("{}")
        (sessions_dir / "s2.jsonl").write_text("{}")
        (sessions_dir / "s3.jsonl").write_text("{}")

        runner._last_scan_time = 0  # Reset scan throttle
        result = await runner.maybe_run("current")
        # Should trigger consolidation (time=inf, sessions=3 >= 2)
        assert result is not None
        assert result.sessions_reviewed >= 2

    @pytest.mark.asyncio
    async def test_force_run(self, setup):
        runner, sessions_dir, _data_dir = setup
        # Force run should bypass time/session gates
        (sessions_dir / "s1.jsonl").write_text("{}")

        result = await runner.force_run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_force_run_no_sessions(self, setup):
        runner, _sessions_dir, _data_dir = setup
        # Record consolidation now so no sessions are "since"
        await runner.lock.record_consolidation()

        result = await runner.force_run()
        assert result is not None
        assert "No sessions" in result.summary


# ------------------------------------------------------------------ #
# DreamResult
# ------------------------------------------------------------------ #


class TestDreamResult:
    def test_default(self):
        r = DreamResult()
        assert r.files_touched == []
        assert r.sessions_reviewed == 0
        assert r.summary == ""
