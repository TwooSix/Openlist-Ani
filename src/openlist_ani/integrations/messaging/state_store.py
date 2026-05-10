from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import OutboundTarget

_TARGETS_FILE = "notification_targets.json"


class MessagingStateStore:
    """Small JSON-backed store for messaging runtime state."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_notification_target(
        self, platform: str, target: OutboundTarget | dict[str, Any]
    ) -> None:
        if isinstance(target, OutboundTarget):
            payload = target.to_dict()
        else:
            payload = dict(target)
        data = self._read_json(self._path(_TARGETS_FILE)) or {}
        data[platform] = payload
        self._write_json(self._path(_TARGETS_FILE), data)

    def load_notification_target(self, platform: str) -> OutboundTarget | None:
        data = self._read_json(self._path(_TARGETS_FILE)) or {}
        target = data.get(platform)
        if not isinstance(target, dict):
            return None
        return OutboundTarget.from_mapping(target)

    def _path(self, name: str) -> Path:
        if name != _TARGETS_FILE:
            raise ValueError(f"Unsupported messaging state file: {name}")
        path = (self.root / name).resolve()
        if path.parent != self.root:
            raise ValueError(f"Messaging state path escapes root: {name}")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(  # NOSONAR - path is a fixed file name under resolved root.
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
