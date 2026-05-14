from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import TERMINAL_STATES
from openlist_ani.logger import logger

DEFAULT_TASK_MEMENTO_DB_PATH = Path.cwd() / "data/task_mementos.db"
LEGACY_JSON_IMPORT_KEY = "legacy_json_imported"


class SqliteTaskMementoStore:
    """SQLite-backed memento store with one durable row per active task."""

    def __init__(
        self,
        path: str | Path = DEFAULT_TASK_MEMENTO_DB_PATH,
        legacy_json_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
        self._initialized = False

    def load_all(self) -> list[TaskMemento]:
        self._ensure_initialized()
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM task_mementos ORDER BY updated_at, task_id"
            ).fetchall()

        tasks: list[TaskMemento] = []
        for (payload,) in rows:
            try:
                tasks.append(TaskMemento.from_dict(json.loads(payload)))
            except Exception as e:
                logger.error(
                    f"Failed to load task memento from {self.path}: {e}. "
                    "The corrupted task will not be restored."
                )
        return tasks

    def save(self, task_memento: TaskMemento) -> None:
        self._ensure_initialized()
        if task_memento.state in TERMINAL_STATES:
            self.delete(task_memento.task_id)
            return

        task_memento.touch()
        payload = json.dumps(task_memento.to_dict(), ensure_ascii=False)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO task_mementos (task_id, state, updated_at, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    task_memento.task_id,
                    task_memento.state.value,
                    task_memento.updated_at,
                    payload,
                ),
            )

    def delete(self, task_id: str) -> None:
        self._ensure_initialized()
        with self._connect() as db:
            db.execute("DELETE FROM task_mementos WHERE task_id = ?", (task_id,))

    def atomic_flush(self) -> None:
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_mementos (
                    task_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_mementos_updated_at "
                "ON task_mementos(updated_at)"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_memento_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """)
            self._import_legacy_json_if_empty(db)
        self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def _import_legacy_json_if_empty(self, db: sqlite3.Connection) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return
        if self._legacy_import_done(db):
            return

        row_count = db.execute("SELECT COUNT(*) FROM task_mementos").fetchone()[0]
        if row_count:
            self._mark_legacy_import_done(db)
            return

        imported = 0
        legacy_tasks, loaded = self._load_legacy_tasks()
        if not loaded:
            return

        for task in legacy_tasks:
            if task.state in TERMINAL_STATES:
                continue
            db.execute(
                """
                INSERT OR REPLACE INTO task_mementos
                    (task_id, state, updated_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.state.value,
                    task.updated_at,
                    json.dumps(task.to_dict(), ensure_ascii=False),
                ),
            )
            imported += 1

        if imported:
            logger.info(
                f"Imported {imported} legacy task mementos from "
                f"{self.legacy_json_path} into {self.path}"
            )
        self._mark_legacy_import_done(db)

    def _legacy_import_done(self, db: sqlite3.Connection) -> bool:
        row = db.execute(
            "SELECT value FROM task_memento_metadata WHERE key = ?",
            (LEGACY_JSON_IMPORT_KEY,),
        ).fetchone()
        return row is not None

    def _mark_legacy_import_done(self, db: sqlite3.Connection) -> None:
        db.execute(
            """
            INSERT INTO task_memento_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (LEGACY_JSON_IMPORT_KEY, str(self.legacy_json_path)),
        )

    def _load_legacy_tasks(self) -> tuple[list[TaskMemento], bool]:
        if self.legacy_json_path is None:
            return [], True

        try:
            raw = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))
            items = raw.get("tasks", [])
        except Exception as e:
            logger.error(
                f"Failed to read legacy task mementos from "
                f"{self.legacy_json_path}: {e}"
            )
            return [], False

        tasks: list[TaskMemento] = []
        for item in items:
            try:
                tasks.append(TaskMemento.from_dict(item))
            except Exception as e:
                logger.error(
                    f"Failed to import legacy task memento from "
                    f"{self.legacy_json_path}: {e}"
                )
        return tasks, True
