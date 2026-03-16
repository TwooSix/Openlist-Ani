from abc import ABC, abstractmethod
from enum import Enum


class BotType(Enum):
    TELEGRAM = "telegram"
    PUSHPLUS = "pushplus"


class BotBase(ABC):
    @abstractmethod
    async def send_message(self, message: str) -> bool: ...
