from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, Dict, Optional


class OpenlistTaskState(Enum):
    Pending = 0
    Running = 1
    Succeeded = 2
    Canceling = 3
    Canceled = 4
    Errored = 5
    Failing = 6
    Failed = 7
    StateWaitingRetry = 8
    StateBeforeRetry = 9


class OfflineDownloadTool(StrEnum):
    ARIA2 = "aria2"
    QBITTORRENT = "qBittorrent"
    PIKPAK = "PikPak"
    CLOUD_115 = "115 Cloud"


def _parse_iso(dt: Optional[str]) -> Optional[datetime]:
    if not dt:
        return None
    s = dt
    # Support trailing Z as UTC
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    # Limit fractional seconds to microseconds (6 digits) and preserve timezone if present
    if "." in s:
        try:
            before, after = s.split(".", 1)
            tz = ""
            if "+" in after or "-" in after:
                idx_plus = after.rfind("+")
                idx_minus = after.rfind("-")
                idx = max(idx_plus, idx_minus)
                if idx != -1:
                    tz = after[idx:]
                    frac = after[:idx]
                else:
                    frac = after
            else:
                frac = after
            frac = frac[:6].ljust(6, "0")
            s = f"{before}.{frac}{tz}"
        except Exception:
            pass

    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


@dataclass
class OpenlistTask:
    id: str
    name: str
    creator: Optional[str] = None
    creator_role: Optional[int] = None
    state: Optional[OpenlistTaskState] = None
    status: Optional[str] = None
    progress: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_bytes: Optional[int] = None
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OpenlistTask":
        # Map state integer to OpenlistTaskState enum when possible
        state_val = d.get("state")
        state_enum = None
        if state_val is not None:
            try:
                state_enum = OpenlistTaskState(state_val)
            except ValueError:
                state_enum = None

        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            creator=d.get("creator"),
            creator_role=d.get("creator_role"),
            state=state_enum,
            status=d.get("status"),
            progress=d.get("progress"),
            start_time=_parse_iso(d.get("start_time")),
            end_time=_parse_iso(d.get("end_time")),
            total_bytes=d.get("total_bytes"),
            error=d.get("error"),
        )


@dataclass
class FileEntry:
    name: str
    path: Optional[str] = None
    size: Optional[int] = None
    is_dir: Optional[bool] = None
    modified: Optional[datetime] = None
    created: Optional[datetime] = None
    sign: Optional[str] = None
    thumb: Optional[str] = None
    type: Optional[int] = None
    hash_info: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FileEntry":
        # prefer structured hash_info; fall back to parsing JSON in `hashinfo` if present
        hash_info = d.get("hash_info")
        if not hash_info and d.get("hashinfo"):
            try:
                import json

                hash_info = json.loads(d.get("hashinfo"))
            except Exception:
                hash_info = None

        return cls(
            name=d.get("name", ""),
            path=d.get("path") or d.get("full_path"),
            size=d.get("size") or d.get("bytes") or d.get("total_bytes"),
            is_dir=d.get("is_dir") if "is_dir" in d else None,
            modified=_parse_iso(d.get("modified")),
            created=_parse_iso(d.get("created")),
            sign=d.get("sign"),
            thumb=d.get("thumb"),
            type=d.get("type"),
            hash_info=hash_info,
        )

    @property
    def is_directory(self) -> bool:
        return bool(self.is_dir)
