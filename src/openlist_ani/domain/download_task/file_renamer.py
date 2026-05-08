from __future__ import annotations

from dataclasses import dataclass

from ..anime_release import AnimeRelease


class FileRenameError(Exception):
    """Raised when a downloaded file cannot be renamed."""


@dataclass
class RenameRequest:
    release: AnimeRelease
    directory_path: str
    source_filename: str
    target_filename: str


@dataclass
class RenamedFile:
    release: AnimeRelease
    directory_path: str
    filename: str

    @property
    def path(self) -> str:
        return f"{self.directory_path.rstrip('/')}/{self.filename}"
