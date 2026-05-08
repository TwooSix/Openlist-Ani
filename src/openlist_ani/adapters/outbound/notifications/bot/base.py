from abc import ABC, abstractmethod


class BotBase(ABC):
    @abstractmethod
    async def send_message(self, message: str) -> bool: ...
