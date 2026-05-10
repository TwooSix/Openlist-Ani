from __future__ import annotations

from openlist_ani import assistant


def test_main_handles_keyboard_interrupt_without_reraising(monkeypatch):
    monkeypatch.setattr(assistant.sys, "argv", ["openlist-ani-assistant"])

    def raise_keyboard_interrupt(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(assistant.asyncio, "run", raise_keyboard_interrupt)

    assistant.main()
