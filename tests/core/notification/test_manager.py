"""Tests for NotificationManager."""

import pytest

from openlist_ani.core.notification.bot.base import BotBase
from openlist_ani.core.notification.bot.telegram import TelegramBot
from openlist_ani.core.notification.manager import NotificationManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBot(BotBase):
    """Concrete bot for testing — records sent messages."""

    def __init__(self, name: str = "fake", should_fail: bool = False):
        super().__init__()
        self.name = name
        self.sent: list[str] = []
        self._should_fail = should_fail

    async def send_message(self, message: str) -> bool:
        if self._should_fail:
            raise RuntimeError("send failed")
        self.sent.append(message)
        return True


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestNotificationManagerInit:
    def test_no_bots(self):
        mgr = NotificationManager(bots=None)
        assert mgr._bots == []

    def test_with_bots(self):
        b = _FakeBot()
        mgr = NotificationManager(bots=[b])
        assert len(mgr._bots) == 1

    def test_add_bot(self):
        mgr = NotificationManager()
        b = _FakeBot()
        mgr.add_bot(b)
        assert b in mgr._bots


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------


class TestSendNotification:
    @pytest.mark.asyncio
    async def test_send_to_single_bot(self):
        bot = _FakeBot("bot1")
        mgr = NotificationManager(bots=[bot])
        results = await mgr.send_notification("hello")
        assert results["_FakeBot"] is True
        assert bot.sent == ["hello"]

    @pytest.mark.asyncio
    async def test_send_to_multiple_bots(self):
        b1 = _FakeBot("b1")
        b2 = _FakeBot("b2")
        mgr = NotificationManager(bots=[b1, b2])
        results = await mgr.send_notification("msg")
        assert results["_FakeBot"] is True
        assert b1.sent == ["msg"]
        assert b2.sent == ["msg"]

    @pytest.mark.asyncio
    async def test_send_no_bots_returns_empty(self):
        mgr = NotificationManager()
        results = await mgr.send_notification("msg")
        assert results == {}

    @pytest.mark.asyncio
    async def test_send_with_failing_bot(self):
        bot = _FakeBot("fail", should_fail=True)
        mgr = NotificationManager(bots=[bot], max_retries=1, retry_backoff=0.01)
        results = await mgr.send_notification("msg")
        assert results["_FakeBot"] is False


# ---------------------------------------------------------------------------
# _send_with_retry
# ---------------------------------------------------------------------------


class TestSendWithRetry:
    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """Bot that fails once then succeeds should succeed after retry."""
        call_count = 0

        class RetryBot(BotBase):
            async def send_message(self, message: str) -> bool:
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise RuntimeError("temporary failure")
                return True

        bot = RetryBot()
        mgr = NotificationManager(bots=[bot], max_retries=3, retry_backoff=0.01)
        result = await mgr._send_with_retry(bot, "test")
        assert result is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_fail(self):
        bot = _FakeBot("fail", should_fail=True)
        mgr = NotificationManager(bots=[bot], max_retries=2, retry_backoff=0.01)
        result = await mgr._send_with_retry(bot, "msg")
        assert result is False


# ---------------------------------------------------------------------------
# Batch notifications
# ---------------------------------------------------------------------------


class TestBatchNotifications:
    @pytest.mark.asyncio
    async def test_batch_queues_messages(self):
        """With batching enabled, messages should be queued, not sent immediately."""
        bot = _FakeBot()
        mgr = NotificationManager(bots=[bot], batch_interval=300.0)
        result = await mgr.send_download_complete_notification(
            "Bocchi the Rock!", "Episode 01"
        )
        assert result == {}
        # Nothing sent yet
        assert bot.sent == []
        # But queue should have the item
        assert len(mgr._bot_queues[bot]) > 0

    @pytest.mark.asyncio
    async def test_batch_flush(self):
        """Flushing batched notifications should send aggregated message."""
        bot = _FakeBot()
        mgr = NotificationManager(bots=[bot], batch_interval=300.0)

        await mgr.send_download_complete_notification("Anime A", "EP 01")
        await mgr.send_download_complete_notification("Anime A", "EP 02")
        await mgr.send_download_complete_notification("Anime B", "EP 05")

        await mgr._send_batched_notifications()

        assert len(bot.sent) == 1
        msg = bot.sent[0]
        assert "Anime A" in msg
        assert "EP 01" in msg
        assert "EP 02" in msg
        assert "Anime B" in msg

    @pytest.mark.asyncio
    async def test_batch_disabled_sends_immediately(self):
        """With batch_interval=0, messages should be sent right away."""
        bot = _FakeBot()
        mgr = NotificationManager(bots=[bot], batch_interval=0)
        result = await mgr.send_download_complete_notification("Anime", "EP 01")
        assert result["_FakeBot"] is True
        assert len(bot.sent) == 1

    @pytest.mark.asyncio
    async def test_batch_queue_cleared_after_send(self):
        bot = _FakeBot()
        mgr = NotificationManager(bots=[bot], batch_interval=300.0)
        await mgr.send_download_complete_notification("A", "ep1")
        await mgr._send_batched_notifications()
        # Queue should be cleared
        assert len(mgr._bot_queues[bot]) == 0

    @pytest.mark.asyncio
    async def test_batch_queue_kept_on_failure(self):
        """If send fails, queue should be kept for retry."""
        bot = _FakeBot("fail", should_fail=True)
        mgr = NotificationManager(
            bots=[bot], batch_interval=300.0, max_retries=1, retry_backoff=0.01
        )
        await mgr.send_download_complete_notification("A", "ep1")
        await mgr._send_batched_notifications()
        # Queue should still have the item
        assert len(mgr._bot_queues[bot]) > 0


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


class TestFromConfig:
    def test_disabled_returns_none(self):
        from openlist_ani.config import NotificationConfig

        cfg = NotificationConfig(enabled=False)
        assert NotificationManager.from_config(cfg) is None

    def test_enabled_but_no_bots_returns_none(self):
        from openlist_ani.config import NotificationConfig

        cfg = NotificationConfig(enabled=True, bots=[])
        assert NotificationManager.from_config(cfg) is None

    def test_enabled_with_telegram_bot(self):
        from openlist_ani.config import BotConfig, NotificationConfig

        cfg = NotificationConfig(
            enabled=True,
            bots=[
                BotConfig(
                    type="telegram",
                    enabled=True,
                    config={"bot_token": "tok", "user_id": 123},
                )
            ],
        )
        mgr = NotificationManager.from_config(cfg)
        assert mgr is not None
        assert len(mgr._bots) == 1
        assert isinstance(mgr._bots[0], TelegramBot)

    def test_enabled_with_pushplus_bot(self):
        from openlist_ani.config import BotConfig, NotificationConfig

        cfg = NotificationConfig(
            enabled=True,
            bots=[
                BotConfig(
                    type="pushplus",
                    enabled=True,
                    config={"user_token": "tok"},
                )
            ],
        )
        mgr = NotificationManager.from_config(cfg)
        assert mgr is not None
        assert len(mgr._bots) == 1

    def test_disabled_bot_skipped(self):
        from openlist_ani.config import BotConfig, NotificationConfig

        cfg = NotificationConfig(
            enabled=True,
            bots=[
                BotConfig(
                    type="telegram",
                    enabled=False,
                    config={"bot_token": "tok", "user_id": 123},
                )
            ],
        )
        mgr = NotificationManager.from_config(cfg)
        # All bots disabled → returns None
        assert mgr is None

    def test_invalid_bot_type_skipped(self):
        from openlist_ani.config import BotConfig, NotificationConfig

        cfg = NotificationConfig(
            enabled=True,
            bots=[
                BotConfig(
                    type="nonexistent_service",
                    enabled=True,
                    config={},
                )
            ],
        )
        mgr = NotificationManager.from_config(cfg)
        assert mgr is None

    def test_batch_interval_from_config(self):
        from openlist_ani.config import BotConfig, NotificationConfig

        cfg = NotificationConfig(
            enabled=True,
            batch_interval=60.0,
            bots=[
                BotConfig(
                    type="telegram",
                    enabled=True,
                    config={"bot_token": "tok", "user_id": 123},
                )
            ],
        )
        mgr = NotificationManager.from_config(cfg)
        assert mgr is not None
        assert mgr._batch_interval == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_stop_sends_pending(self):
        """Stop should flush pending notifications."""
        bot = _FakeBot()
        mgr = NotificationManager(bots=[bot], batch_interval=300.0)
        await mgr.send_download_complete_notification("A", "ep1")

        # Don't start the background worker, just test stop flushes
        await mgr.stop()
        assert len(bot.sent) == 1
