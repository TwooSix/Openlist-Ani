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
        season_str = f"S{self.season:02d}" if self.season is not None else "S??"
        episode_str = f"E{self.episode:02d}" if self.episode is not None else "E??"
        lang_str = (
            "/".join(str(language) for language in self.languages)
            if self.languages
            else "?"
        )
        parts = [
            f"[{self.anime_name or '?'}]",
            f"{season_str}{episode_str}",
            f"v{self.version}" if self.version > 1 else "",
            f"| {self.fansub}" if self.fansub else "",
            (
                f"| {self.quality}"
                if self.quality and self.quality != VideoQuality.UNKNOWN
                else ""
            ),
            f"| {lang_str}",
            f"| {self.title}",
        ]
        return " ".join(p for p in parts if p)
