from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class VideoQuality(StrEnum):
    k2160p = "2160p"
    k1080p = "1080p"
    k720p = "720p"
    k480p = "480p"
    kUnknown = "unknown"


class LanguageType(StrEnum):
    kChs = "简"
    kCht = "繁"
    kJp = "日"
    kEng = "英"
    kUnknown = "未知"


@dataclass
class AnimeResourceInfo:
    """
    Data structure for RSS parsing results.
    """

    title: str
    download_url: str
    anime_name: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    fansub: Optional[str] = None
    quality: Optional[VideoQuality] = VideoQuality.kUnknown
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
