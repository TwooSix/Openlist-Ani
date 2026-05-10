from __future__ import annotations

from openlist_ani.integrations.messaging.models import OutboundTarget
from openlist_ani.integrations.messaging.state_store import MessagingStateStore


def test_notification_target_round_trip(tmp_path):
    store = MessagingStateStore(tmp_path)
    target = OutboundTarget(
        platform="feishu",
        chat_id="oc_123",
        chat_type="group",
        user_id="ou_456",
        receive_id_type="chat_id",
    )

    store.save_notification_target("feishu", target)

    assert store.load_notification_target("feishu") == target
