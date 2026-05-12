from __future__ import annotations

from io import StringIO
from pathlib import Path

import openlist_ani.logger as logger_module


class AutoTagExample:
    def emit(self):
        logger_module.logger.info("class tagged")


def test_sanitize_for_log_redacts_sensitive_urls_and_tokens():
    message = (
        "proxy=http://user:password@example.com:8080 "
        "rss=https://mikan.example/rss?token=secret&user=abc "
        "tmdb=https://api.themoviedb.org/3/search?api_key=tmdb-secret&query=test "
        "magnet=magnet:?xt=urn:btih:ABCDEF1234567890&tr=https://tracker/passkey "
        "telegram=https://api.telegram.org/bot123456:secret/sendMessage "
        "push=http://www.pushplus.plus/send/push-secret"
    )

    sanitized = logger_module.sanitize_for_log(message)

    assert "password" not in sanitized
    assert "secret" not in sanitized
    assert "tmdb-secret" not in sanitized
    assert "push-secret" not in sanitized
    assert "tracker/passkey" not in sanitized
    assert "user:password@" not in sanitized
    assert "token=<redacted>" in sanitized
    assert "api_key=<redacted>" in sanitized
    assert "bot<redacted>" in sanitized
    assert "send/<redacted>" in sanitized


def test_configure_logger_uses_clickable_source_location(monkeypatch, tmp_path):
    sink = StringIO()
    monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logger_module, "stdout", sink)

    logger_module.configure_logger(level="INFO", log_name="test", file_logging=True)
    try:
        logger_module.logger.info("compact format")
    finally:
        logger_module.configure_logger()

    output = sink.getvalue()
    assert "[test_logger]" in output
    assert "compact format" in output
    assert "tests/test_logger.py:" in output
    assert str(Path.cwd()) not in output
    assert "test_configure_logger_uses_clickable_source_location" not in output


def test_logger_sanitizes_messages(monkeypatch, tmp_path):
    sink = StringIO()
    monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logger_module, "stdout", sink)

    logger_module.configure_logger(level="INFO", log_name="test", file_logging=True)
    try:
        logger_module.logger.info(
            "fetch failed: https://example.com/rss?token=plain-secret"
        )
    finally:
        logger_module.configure_logger()

    output = sink.getvalue()
    assert "plain-secret" not in output
    assert "token=<redacted>" in output


def test_logger_uses_enclosing_class_name_as_tag(monkeypatch, tmp_path):
    sink = StringIO()
    monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logger_module, "stdout", sink)

    logger_module.configure_logger(level="INFO", log_name="test")
    try:
        AutoTagExample().emit()
    finally:
        logger_module.configure_logger()

    output = sink.getvalue()
    assert "[AutoTagExample]" in output
    assert "tests/test_logger.py:" in output
    assert str(Path.cwd()) not in output
    assert "class tagged" in output


def test_bound_tag_overrides_auto_tag(monkeypatch, tmp_path):
    sink = StringIO()
    monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logger_module, "stdout", sink)

    logger_module.configure_logger(level="INFO", log_name="test")
    try:
        logger_module.logger.bind(tag="rss").info("RSS scan started")
    finally:
        logger_module.configure_logger()

    output = sink.getvalue()
    assert "[rss]" in output
    assert "[test_logger]" not in output
    assert "RSS scan started" in output


def test_console_logger_uses_colorized_format(monkeypatch, tmp_path):
    calls = []

    def fake_add(*args, **kwargs):
        calls.append(kwargs)
        return len(calls)

    monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logger_module.logger, "add", fake_add)
    monkeypatch.setattr(logger_module.logger, "remove", lambda: None)

    logger_module.configure_logger(level="INFO", log_name="test", file_logging=True)

    console_call = calls[0]
    file_call = calls[1]
    assert console_call["colorize"] is True
    assert "<level>" in console_call["format"]
    assert "<cyan><u>{extra[source_location]}</u></cyan>" in console_call["format"]
    assert (
        "<level><magenta>[{extra[tag]}]</magenta> {message}</level>"
        in console_call["format"]
    )
    assert console_call["format"].index("{extra[source_location]}") < console_call[
        "format"
    ].index("{message}")
    assert file_call["format"].index("{extra[source_location]}") < file_call[
        "format"
    ].index("{message}")
    assert "colorize" not in file_call


def test_configure_logger_respects_file_logging_env(monkeypatch, tmp_path):
    calls = []

    def fake_add(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls)

    monkeypatch.setenv("OPENLIST_ANI_FILE_LOGGING", "0")
    monkeypatch.setattr(logger_module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logger_module.logger, "add", fake_add)
    monkeypatch.setattr(logger_module.logger, "remove", lambda: None)

    logger_module.configure_logger(level="INFO", log_name="test")

    assert len(calls) == 1
    assert calls[0][0][0] is logger_module.stdout
