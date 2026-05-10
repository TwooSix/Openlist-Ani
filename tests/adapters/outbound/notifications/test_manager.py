from __future__ import annotations

import pytest

from openlist_ani.adapters.outbound.notifications.bot.base import BotBase
from openlist_ani.adapters.outbound.notifications.manager import NotificationManager


class StartTrackingBot(BotBase):
    def __init__(self) -> None:
        self.started = 0

    async def start(self) -> None:
        self.started += 1

    async def send_message(self, message: str) -> bool:
        return True


class FailingStartBot(BotBase):
    async def start(self) -> None:
        raise RuntimeError("startup failed")

    async def send_message(self, message: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_manager_start_invokes_bot_start_even_without_batching():
    bot = StartTrackingBot()
    manager = NotificationManager([bot], batch_interval=0)

    await manager.start()

    assert bot.started == 1
    await manager.stop()


@pytest.mark.asyncio
async def test_manager_start_propagates_bot_start_failure():
    manager = NotificationManager([FailingStartBot()], batch_interval=0)

    with pytest.raises(RuntimeError, match="startup failed"):
        await manager.start()
