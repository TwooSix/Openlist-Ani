from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .downloader import DownloaderMemento
from .task import DownloadState
from ..anime_release import AnimeRelease, LanguageType, VideoQuality

SCHEMA_VERSION = 1


@dataclass
class PipelineMemento:
    next_buffer: str = "download"
    downloaded_directory_path: str | None = None
    downloaded_filename: str | None = None
    renamed_path: str | None = None


@dataclass
class RetryMemento:
    retry_count: int = 0
    max_retries: int = 3
    last_error: str | None = None


@dataclass
class TaskMemento:
    task_id: str
    state: DownloadState
    release: AnimeRelease
    base_path: str
    downloader: DownloaderMemento | None = None
    pipeline: PipelineMemento = field(default_factory=PipelineMemento)
    retry: RetryMemento = field(default_factory=RetryMemento)
    output_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    schema_version: int = SCHEMA_VERSION

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["release"] = _release_to_dict(self.release)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskMemento:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported memento schema: {data.get('schema_version')}"
            )

        downloader_data = data.get("downloader")
        return cls(
            task_id=data["task_id"],
            state=DownloadState(data["state"]),
            release=_release_from_dict(data["release"]),
            base_path=data["base_path"],
            downloader=(
                DownloaderMemento(**downloader_data) if downloader_data else None
            ),
            pipeline=PipelineMemento(**data.get("pipeline", {})),
            retry=RetryMemento(**data.get("retry", {})),
            output_path=data.get("output_path"),
            created_at=data.get("created_at") or datetime.now().isoformat(),
            updated_at=data.get("updated_at") or datetime.now().isoformat(),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


def _release_to_dict(release: AnimeRelease) -> dict[str, Any]:
    return {
        "title": release.title,
        "download_url": release.download_url,
        "anime_name": release.anime_name,
        "season": release.season,
        "episode": release.episode,
        "fansub": release.fansub,
        "quality": release.quality.value if release.quality else None,
        "languages": [language.value for language in release.languages],
        "version": release.version,
    }


def _release_from_dict(data: dict[str, Any]) -> AnimeRelease:
    quality = data.get("quality")
    languages = data.get("languages") or []
    return AnimeRelease(
        title=data["title"],
        download_url=data["download_url"],
        anime_name=data.get("anime_name"),
        season=data.get("season"),
        episode=data.get("episode"),
        fansub=data.get("fansub"),
        quality=VideoQuality(quality) if quality else None,
        languages=[
            LanguageType(language) if isinstance(language, str) else language
            for language in languages
        ],
        version=data.get("version") or 1,
    )
