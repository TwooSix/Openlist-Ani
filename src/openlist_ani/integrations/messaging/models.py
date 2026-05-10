from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OutboundTarget:
    platform: str
    chat_id: str
    chat_type: str = "dm"
    user_id: str = ""
    receive_id_type: str | None = None
    display_name: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "OutboundTarget":
        return cls(
            platform=str(value.get("platform") or ""),
            chat_id=str(value.get("chat_id") or ""),
            chat_type=str(value.get("chat_type") or "dm"),
            user_id=str(value.get("user_id") or ""),
            receive_id_type=(
                str(value["receive_id_type"]) if value.get("receive_id_type") else None
            ),
            display_name=str(value.get("display_name") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "platform": self.platform,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "user_id": self.user_id,
            "display_name": self.display_name,
        }
        if self.receive_id_type:
            data["receive_id_type"] = self.receive_id_type
        return data


@dataclass(frozen=True)
class InboundMessage:
    platform: str
    text: str
    target: OutboundTarget
    message_id: str | None = None
    raw: dict[str, Any] | None = None
