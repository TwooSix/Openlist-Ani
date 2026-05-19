from __future__ import annotations

from io import StringIO

from openlist_ani import assistant
from openlist_ani.assistant.core import loop as loop_module


def test_main_handles_keyboard_interrupt_without_reraising(monkeypatch):
    monkeypatch.setattr(assistant.sys, "argv", ["openlist-ani-assistant"])

    def raise_keyboard_interrupt(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(assistant.asyncio, "run", raise_keyboard_interrupt)

    assistant.main()


def test_main_respects_disabled_file_logging(monkeypatch):
    add_calls = []

    def fake_add(*args, **kwargs):
        add_calls.append((args, kwargs))
        return len(add_calls)

    def fake_remove():
        return None

    def raise_keyboard_interrupt(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setenv("OPENLIST_ANI_FILE_LOGGING", "0")
    monkeypatch.setattr(assistant.sys, "argv", ["openlist-ani-assistant", "--cli"])
    monkeypatch.setattr(assistant.logger, "add", fake_add)
    monkeypatch.setattr(assistant.logger, "remove", fake_remove)
    monkeypatch.setattr(assistant.logger, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(assistant.asyncio, "run", raise_keyboard_interrupt)

    assistant.main()

    assert add_calls == []


def test_agentic_loop_logger_uses_assistant_log_extras():
    sink = StringIO()
    handler_id = assistant.logger.add(
        sink,
        level="INFO",
        format=assistant.LOG_FORMAT,
        colorize=False,
    )

    try:
        loop_module.logger.info("AgenticLoop logger smoke test")
    finally:
        assistant.logger.remove(handler_id)

    assert "AgenticLoop logger smoke test" in sink.getvalue()
