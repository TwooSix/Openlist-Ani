import asyncio
from abc import ABC, abstractmethod


class BotBase(ABC):
    async def start(self) -> None:
        """Run optional bot startup work."""
        await asyncio.sleep(0)

    @abstractmethod
    async def send_message(self, message: str) -> bool: ...
