from dataclasses import dataclass, field
from enum import StrEnum


class VideoQuality(StrEnum):
    Q2160P = "2160p"
    Q1080P = "1080p"
    Q720P = "720p"
    Q480P = "480p"
    UNKNOWN = "unknown"


class LanguageType(StrEnum):
    CHS = "简"
    CHT = "繁"
    JP = "日"
    ENG = "英"
    UNKNOWN = "未知"


@dataclass
class AnimeResourceInfo:
    """
    Data structure for RSS parsing results.
    """

    title: str
    download_url: str
    anime_name: str | None = None
    season: int | None = None
    episode: int | None = None
    fansub: str | None = None
    quality: VideoQuality | None = VideoQuality.UNKNOWN
    languages: list[LanguageType] = field(default_factory=list)
    version: int = 1

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(\n"
            f"    title={self.title!r},\n"
            f"    anime_name={self.anime_name!r},\n"
            f"    season={self.season},\n"
            f"    episode={self.episode},\n"
            f"    fansub={self.fansub!r},\n"
            f"    quality={self.quality},\n"
            f"    languages={self.languages}\n"
            f"    version={self.version}\n"
            f")"
        )
