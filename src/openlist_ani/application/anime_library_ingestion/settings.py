from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetadataFilterSettings:
    exclude_fansub: list[str] = field(default_factory=list)
    exclude_quality: list[str] = field(default_factory=list)
    exclude_languages: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PrioritySettings:
    field_order: list[str] = field(
        default_factory=lambda: ["fansub", "quality", "languages"]
    )
    fansub: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    quality: list[str] = field(
        default_factory=lambda: ["2160p", "1080p", "720p", "480p"]
    )


@dataclass(frozen=True)
class AnimeLibraryIngestionSettings:
    download_path: str
    rename_format: str
    rss_interval_seconds: float
    download_concurrency: int = 3
    strict_filtering: bool = False
    metadata_filter: MetadataFilterSettings = field(
        default_factory=MetadataFilterSettings
    )
    priority: PrioritySettings = field(default_factory=PrioritySettings)
