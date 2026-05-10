from __future__ import annotations

from openlist_ani.integrations.messaging.feishu import FeishuMessenger
from openlist_ani.integrations.messaging.state_store import MessagingStateStore

from .base import BotBase


class FeishuBot(BotBase):
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        receive_id: str | None = None,
        receive_id_type: str | None = None,
        domain: str = "feishu",
        state_dir: str = "data/messaging",
        messenger: FeishuMessenger | None = None,
    ) -> None:
        self.receive_id = receive_id
        self.receive_id_type = receive_id_type
        self._messenger = messenger or FeishuMessenger(
            app_id=app_id,
            app_secret=app_secret,
            domain=domain,
            store=MessagingStateStore(state_dir),
        )

    async def send_message(self, message: str) -> bool:
        return await self._messenger.send_text(
            self.receive_id,
            message,
            receive_id_type=self.receive_id_type,
        )
