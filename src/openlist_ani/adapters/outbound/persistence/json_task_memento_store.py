from __future__ import annotations

import json
import os
from pathlib import Path

from openlist_ani.domain.download_task.memento import TaskMemento
from openlist_ani.domain.download_task.task import TERMINAL_STATES
from openlist_ani.logger import logger


class JsonTaskMementoStore:
    """JSON-backed versioned memento store with atomic flush."""

    def __init__(self, path: str | Path = "data/task_mementos.json") -> None:
        self.path = Path(path)
        self._items: dict[str, TaskMemento] = {}
        self._loaded = False

    def load_all(self) -> list[TaskMemento]:
        if not self.path.exists():
            self._items = {}
            self._loaded = True
            return []

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            items = raw.get("tasks", [])
            self._items = {
                item.task_id: item for item in (TaskMemento.from_dict(i) for i in items)
            }
            self._loaded = True
            return list(self._items.values())
        except Exception as e:
            logger.error(
                f"Failed to load task mementos from {self.path}: {e}. "
                "Unfinished historical tasks will not be restored."
            )
            self._items = {}
            self._loaded = True
            return []

    def save(self, task_memento: TaskMemento) -> None:
        self._ensure_loaded()
        if task_memento.state in TERMINAL_STATES:
            self._items.pop(task_memento.task_id, None)
            self.atomic_flush()
            return

        task_memento.touch()
        self._items[task_memento.task_id] = task_memento
        self.atomic_flush()

    def delete(self, task_id: str) -> None:
        self._ensure_loaded()
        self._items.pop(task_id, None)
        self.atomic_flush()

    def atomic_flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "tasks": [item.to_dict() for item in self._items.values()],
        }
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_all()
