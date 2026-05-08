from openlist_ani.adapters.outbound.events import OAniEventManager
from openlist_ani.application.common import OAniEvent, OAniEventType


async def test_event_manager_dispatches_and_keeps_history():
    manager = OAniEventManager(history_limit=10)
    received = []

    def handler(event):
        received.append(event.payload["task_id"])

    await manager.subscribe(OAniEventType.TASK_CREATED, handler)
    await manager.start()
    await manager.publish(OAniEvent(OAniEventType.TASK_CREATED, {"task_id": "task-1"}))
    await manager.drain()

    assert received == ["task-1"]
    assert [event.payload["task_id"] for event in await manager.history()] == ["task-1"]
    await manager.stop()


async def test_handler_failure_does_not_block_other_handlers():
    manager = OAniEventManager()
    received = []

    def bad_handler(_event):
        raise RuntimeError("boom")

    def good_handler(event):
        received.append(event.payload["task_id"])

    await manager.subscribe(OAniEventType.TASK_CREATED, bad_handler)
    await manager.subscribe(OAniEventType.TASK_CREATED, good_handler)
    await manager.start()
    await manager.publish(OAniEvent(OAniEventType.TASK_CREATED, {"task_id": "task-1"}))
    await manager.drain()

    assert received == ["task-1"]
    await manager.stop()
