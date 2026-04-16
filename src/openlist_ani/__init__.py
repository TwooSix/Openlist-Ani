"""
OpenList-Ani — Automated anime RSS downloader.

Package layout:
  backend/    Backend process: FastAPI API server + RSS/download workers
  assistant/  Telegram assistant process (communicates with backend via HTTP)
  core/       Domain logic: download, parser, mikan, bangumi, notification, website
  config.py   Shared configuration management
  database.py Shared database access
  logger.py   Shared logging utilities
  scripts/    One-off maintenance scripts
"""


def __getattr__(name: str):
    if name in ("main", "run"):
        from .backend.main import main, run

        globals()["main"] = main
        globals()["run"] = run
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main", "run"]
