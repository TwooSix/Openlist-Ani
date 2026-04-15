from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any


class OpenlistTaskState(Enum):
    PENDING = 0
    RUNNING = 1
    SUCCEEDED = 2
    CANCELING = 3
    CANCELED = 4
    ERRORED = 5
    FAILING = 6
    FAILED = 7
    STATE_WAITING_RETRY = 8
    STATE_BEFORE_RETRY = 9


class OfflineDownloadTool(StrEnum):
    ARIA2 = "aria2"
    QBITTORRENT = "qBittorrent"
    PIKPAK = "PikPak"
    CLOUD115 = "115 Cloud"
    CLOUD115_OPEN = "115 Open"
    PAN123 = "123Pan"
    PAN123_OPEN = "123 Open"
    SIMPLE_HTTP = "SimpleHttp"
    THUNDER = "Thunder"
    THUNDER_BROWSER = "ThunderBrowser"
    THUNDERX = "ThunderX"
    TRANSMISSION = "Transmission"


def _parse_iso(dt: str | None) -> datetime | None:
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
    creator: str | None = None
    creator_role: int | None = None
    state: OpenlistTaskState | None = None
    status: str | None = None
    progress: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_bytes: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OpenlistTask":
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
    path: str | None = None
    size: int | None = None
    is_dir: bool | None = None
    modified: datetime | None = None
    created: datetime | None = None
    sign: str | None = None
    thumb: str | None = None
    type: int | None = None
    hash_info: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FileEntry":
        """Build from an OpenList ``/api/fs/list`` response item.

        OpenList returns both ``hashinfo`` (JSON string) and ``hash_info``
        (structured dict).  We prefer the dict and fall back to parsing
        the string.
        """
        hash_info = d.get("hash_info")
        if not hash_info and d.get("hashinfo"):
            try:
                import json

                hash_info = json.loads(d.get("hashinfo"))
            except Exception:
                hash_info = None

        return cls(
            name=d.get("name", ""),
            path=d.get("path"),
            size=d.get("size"),
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
